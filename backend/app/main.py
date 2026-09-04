"""
Relief Studio — Backend API (Phases 4 + 5)
Server escalation layer + auth/billing/persistence.
Only handles what the browser can't: heavy jobs, AI depth, cloud persistence.

Stack: FastAPI + Postgres (SQLAlchemy) + Redis (job queue) + S3 (file storage)
"""
import os, re, sys, uuid, hashlib, hmac, time, json, threading
from datetime import datetime, timedelta
from typing import Optional

from fastapi import (FastAPI, HTTPException, Depends, UploadFile, File, Header,
                     BackgroundTasks, Request, Body)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, EmailStr, ConfigDict

# ---- Config (env vars in production) ----
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://relief:relief@localhost:5432/relief")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
S3_BUCKET = os.getenv("S3_BUCKET", "relief-studio-files")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")  # empty = AWS default
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_JOB_SETTINGS_MB = int(os.getenv("MAX_JOB_SETTINGS_MB", "16"))
FREE_HEAVY_JOBS_PER_DAY = int(os.getenv("FREE_HEAVY_JOBS", "3"))
PRO_HEAVY_JOBS_PER_DAY = int(os.getenv("PRO_HEAVY_JOBS", "100"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
ENV = os.getenv("ENV", "dev")

# S1 FIX: the JWT signing key silently fell back to a published constant, so a
# deployment that forgot to set SECRET_KEY would accept tokens anyone can mint.
# Refuse to boot in production rather than run forgeable.
if ENV == "production" and SECRET_KEY == "dev-secret-change-in-production":
    sys.exit("FATAL: SECRET_KEY must be set to a unique random value when ENV=production")
if ENV == "production" and CORS_ORIGINS == ["*"]:
    sys.exit("FATAL: CORS_ORIGINS must list explicit origins when ENV=production")

# ---- App ----
app = FastAPI(title="Relief Studio API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])


# ===========================================================================
# DATABASE MODELS (SQLAlchemy ORM)
# ===========================================================================
from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
import enum

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class PlanTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    name = Column(String(100))
    plan = Column(SAEnum(PlanTier), default=PlanTier.FREE)
    created_at = Column(DateTime, default=datetime.utcnow)
    stripe_customer_id = Column(String(255))  # Phase 5 billing
    projects = relationship("Project", back_populates="user", cascade="all,delete-orphan")


class Project(Base):
    __tablename__ = "projects"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    settings_json = Column(Text)  # full project state (depth params, tool config, etc.)
    source_image_key = Column(String(500))  # S3 key
    thumbnail_key = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", back_populates="projects")
    jobs = relationship("Job", back_populates="project", cascade="all,delete-orphan")


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobType(str, enum.Enum):
    HEAVY_TOOLPATH = "heavy_toolpath"  # large grid, server-side CAM
    AI_DEPTH = "ai_depth"              # GPU monocular depth estimation


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    job_type = Column(SAEnum(JobType), nullable=False)
    status = Column(SAEnum(JobStatus), default=JobStatus.QUEUED)
    progress = Column(Integer, default=0)  # 0-100
    settings_json = Column(Text)
    result_key = Column(String(500))   # S3 key for output file
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    project = relationship("Project", back_populates="jobs")


# S2 FIX: create_all ran at import time, so the API process refused to even
# import if Postgres was not up yet (a guaranteed crash-loop on a cold stack)
# and there is no migration path once the schema changes. Do it on startup,
# tolerate a not-yet-ready database, and skip entirely in production where
# schema changes belong in a migration tool.
@app.on_event("startup")
def _init_schema():
    if ENV == "production":
        return
    for attempt in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception as e:
            if attempt == 9:
                print(f"WARNING: could not create tables: {e}", flush=True)
                return
            time.sleep(2)


# ===========================================================================
# AUTH (JWT-based, Phase 5)
# ===========================================================================
import jwt

def hash_password(pw: str) -> str:
    salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${h}"

def verify_password(pw: str, stored: str) -> bool:
    # S3 FIX: a malformed stored hash raised ValueError -> HTTP 500, which both
    # leaks that the row is corrupt and turns a failed login into an outage.
    try:
        salt, h = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(
        hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex(), h
    )


# S4 FIX: no rate limit on /auth/login at all, so the endpoint was an open
# credential-stuffing oracle. Simple in-process sliding window; swap for a
# Redis counter when the API runs more than one replica.
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_LOCK = threading.Lock()
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))
LOGIN_WINDOW_SEC = int(os.getenv("LOGIN_WINDOW_SEC", "300"))

def check_login_rate(key: str):
    now = time.time()
    with _LOGIN_LOCK:
        hits = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < LOGIN_WINDOW_SEC]
        if len(hits) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "Too many login attempts. Try again later.")
        hits.append(now)
        _LOGIN_ATTEMPTS[key] = hits
        if len(_LOGIN_ATTEMPTS) > 10_000:      # bound memory
            for k in [k for k, v in _LOGIN_ATTEMPTS.items()
                      if not any(now - t < LOGIN_WINDOW_SEC for t in v)]:
                _LOGIN_ATTEMPTS.pop(k, None)

def create_token(user_id: str, hours: int = 72) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.utcnow() + timedelta(hours=hours)},
        SECRET_KEY, algorithm="HS256"
    )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# S8 FIX: every S3 caller built its own client, and the two workers built theirs
# with no endpoint_url at all - so in the shipped compose stack they bypassed
# MinIO for real AWS, failed, and silently fell back to a /tmp path the API
# could not serve. One factory, one bucket, honoured everywhere.
def s3_client():
    import boto3
    return boto3.client("s3", endpoint_url=S3_ENDPOINT or None)

def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid token")
    try:
        payload = jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
        user = db.query(User).filter(User.id == payload["sub"]).first()
        if not user:
            raise HTTPException(401, "User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")


# ===========================================================================
# PYDANTIC SCHEMAS
# ===========================================================================
class SignupReq(BaseModel):
    # S5 FIX: `email: str` accepted any string as an account identifier.
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: Optional[str] = Field(default=None, max_length=100)

class LoginReq(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)

class TokenResp(BaseModel):
    token: str
    user_id: str
    email: str
    plan: str

class ProjectCreate(BaseModel):
    name: str
    settings_json: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    settings_json: Optional[str] = None

class ProjectResp(BaseModel):
    # S6 FIX: without from_attributes, Pydantic v2 cannot validate a SQLAlchemy
    # row against this model, so every /projects route raised a response
    # validation error at runtime.
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    settings_json: Optional[str]
    created_at: datetime
    updated_at: datetime

class JobSubmit(BaseModel):
    project_id: str
    job_type: JobType
    settings_json: Optional[str] = None

class JobResp(BaseModel):
    id: str
    status: JobStatus
    progress: int
    error_message: Optional[str]
    result_url: Optional[str] = None


# ===========================================================================
# AUTH ROUTES
# ===========================================================================
@app.post("/auth/signup", response_model=TokenResp)
def signup(req: SignupReq, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(409, "Email already registered")
    user = User(email=req.email, password_hash=hash_password(req.password), name=req.name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResp(token=create_token(user.id), user_id=user.id, email=user.email, plan=user.plan.value)

@app.post("/auth/login", response_model=TokenResp)
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)):
    check_login_rate(f"{request.client.host if request.client else '?'}|{req.email}")
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return TokenResp(token=create_token(user.id), user_id=user.id, email=user.email, plan=user.plan.value)

@app.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "plan": user.plan.value}


# ===========================================================================
# PROJECT ROUTES (Phase 5 — cloud persistence)
# ===========================================================================
@app.get("/projects", response_model=list[ProjectResp])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.user_id == user.id).order_by(Project.updated_at.desc()).all()

@app.post("/projects", response_model=ProjectResp, status_code=201)
def create_project(req: ProjectCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = Project(user_id=user.id, name=req.name, settings_json=req.settings_json)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return proj

@app.get("/projects/{pid}", response_model=ProjectResp)
def get_project(pid: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == pid, Project.user_id == user.id).first()
    if not proj:
        raise HTTPException(404, "Project not found")
    return proj

@app.patch("/projects/{pid}", response_model=ProjectResp)
def update_project(pid: str, req: ProjectUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == pid, Project.user_id == user.id).first()
    if not proj:
        raise HTTPException(404, "Project not found")
    if req.name is not None:
        proj.name = req.name
    if req.settings_json is not None:
        proj.settings_json = req.settings_json
    db.commit()
    db.refresh(proj)
    return proj

@app.delete("/projects/{pid}", status_code=204)
def delete_project(pid: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proj = db.query(Project).filter(Project.id == pid, Project.user_id == user.id).first()
    if not proj:
        raise HTTPException(404, "Project not found")
    # S13 FIX: this was a TODO, so deleting a project orphaned every S3 object it
    # owned - unbounded storage cost and user data retained after an explicit
    # delete. Remove the objects this project actually references.
    keys = [k for k in ([proj.source_image_key, proj.thumbnail_key] +
                        [j.result_key for j in proj.jobs]) if k and not k.startswith("local:")]
    if keys:
        try:
            s3 = s3_client()
            for i in range(0, len(keys), 1000):
                s3.delete_objects(Bucket=S3_BUCKET,
                                  Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})
        except Exception as e:
            # Never block the delete on storage; log for the reaper to retry.
            print(f"WARNING: orphaned S3 keys for project {proj.id}: {e}", flush=True)
    db.delete(proj)
    db.commit()


# ===========================================================================
# UPLOAD ROUTE (pre-signed URL pattern — browser uploads directly to S3)
# ===========================================================================
ALLOWED_UPLOAD_TYPES = {"image/png", "image/jpeg", "image/webp"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@app.post("/uploads/presign")
def get_upload_url(
    filename: str,
    content_type: str = "image/png",
    user: User = Depends(get_current_user),
):
    """Return a pre-signed S3 POST policy so the browser uploads directly,
    not through this server. Saves bandwidth and keeps the API thin."""
    # S7 FIX: `filename` came straight off the query string into the S3 key with
    # no sanitisation, `content_type` was whatever the caller claimed, and
    # MAX_UPLOAD_MB was declared but never enforced anywhere - a presigned PUT
    # carries no size limit, so the cap was decorative. Sanitise the name,
    # allowlist the type, and switch to a presigned POST whose policy carries a
    # real content-length-range the storage backend enforces.
    if content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(400, f"Unsupported content type. Allowed: {sorted(ALLOWED_UPLOAD_TYPES)}")
    clean = _SAFE_NAME.sub("_", os.path.basename(filename or "upload"))[:120].lstrip(".")
    if not clean:
        clean = "upload"

    key = f"uploads/{user.id}/{uuid.uuid4().hex}/{clean}"
    post = s3_client().generate_presigned_post(
        Bucket=S3_BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_UPLOAD_MB * 1024 * 1024],
        ],
        ExpiresIn=3600,
    )
    return {"upload_url": post["url"], "fields": post["fields"],
            "key": key, "max_bytes": MAX_UPLOAD_MB * 1024 * 1024}


# ===========================================================================
# JOB ROUTES (Phase 4 — server escalation for heavy/AI jobs)
# ===========================================================================
@app.post("/jobs", response_model=JobResp, status_code=202)
def submit_job(
    req: JobSubmit,
    bg: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enqueue a heavy compute job. Returns immediately with job_id;
    client polls /jobs/{id} for status or connects via WebSocket (v2)."""

    # verify project ownership
    proj = db.query(Project).filter(Project.id == req.project_id, Project.user_id == user.id).first()
    if not proj:
        raise HTTPException(404, "Project not found")

    # S9 FIX: settings_json carries the whole flattened heightfield. A 2000x2000
    # grid is ~4M floats, tens of MB of JSON, written into a TEXT column with no
    # cap at all - an unbounded DB-bloat and memory DoS. Bound it explicitly.
    if req.settings_json and len(req.settings_json.encode()) > MAX_JOB_SETTINGS_MB * 1024 * 1024:
        raise HTTPException(413, f"Job settings exceed {MAX_JOB_SETTINGS_MB}MB. "
                                 "Upload the heightfield to storage and pass its key instead.")

    # S10 FIX: the limit counted EVERY job the user had ever submitted today
    # regardless of type, while the message and the env vars both describe a
    # heavy-job budget. Count the type actually being charged for.
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_jobs = db.query(Job).filter(
        Job.user_id == user.id,
        Job.job_type == req.job_type,
        Job.created_at >= today_start,
    ).count()
    limit = PRO_HEAVY_JOBS_PER_DAY if user.plan != PlanTier.FREE else FREE_HEAVY_JOBS_PER_DAY
    if today_jobs >= limit:
        raise HTTPException(429, f"Daily {req.job_type.value} limit reached ({limit}). Upgrade for more.")

    job = Job(
        project_id=req.project_id,
        user_id=user.id,
        job_type=req.job_type,
        settings_json=req.settings_json,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Enqueue to Redis (production) or run inline (dev)
    bg.add_task(dispatch_job, job.id)

    return JobResp(id=job.id, status=job.status, progress=0)


@app.get("/jobs/{jid}", response_model=JobResp)
def get_job(jid: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == jid, Job.user_id == user.id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    result_url = None
    if job.status == JobStatus.DONE and job.result_key:
        # S12 FIX: a worker that fell back to local disk stored "local:/tmp/..."
        # here, and this route happily signed it into a download URL that could
        # never resolve. Only sign real object keys; surface the rest honestly.
        if job.result_key.startswith("local:"):
            return JobResp(id=job.id, status=job.status, progress=job.progress,
                           error_message="Result was written to worker-local storage and is not "
                                         "downloadable. Configure S3/MinIO credentials.",
                           result_url=None)
        try:
            result_url = s3_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET, "Key": job.result_key},
                ExpiresIn=3600,
            )
        except Exception as e:
            raise HTTPException(503, f"Storage unavailable: {str(e)[:200]}")
    return JobResp(
        id=job.id, status=job.status, progress=job.progress,
        error_message=job.error_message, result_url=result_url,
    )


ALLOW_INLINE_JOBS = os.getenv("ALLOW_INLINE_JOBS", "1" if ENV != "production" else "0") == "1"


def dispatch_job(job_id: str):
    """Push the job onto the Redis queue for a worker to pick up.

    S11 FIX: the old version caught every exception from the Redis push and
    silently ran the full heavy CAM job inside the API process instead. A brief
    Redis blip therefore turned the thin API into an unbounded CPU worker with
    no queue, no limits and no visibility. Now the inline path is opt-in and off
    by default in production; otherwise the job is marked FAILED so the user
    actually learns the queue is down."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=5)
        r.rpush("relief:jobs", json.dumps({"job_id": job_id}))
        return
    except Exception as e:
        queue_error = str(e)[:300]

    if not ALLOW_INLINE_JOBS:
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = JobStatus.FAILED
                job.error_message = f"Job queue unavailable: {queue_error}"
                db.commit()
        finally:
            db.close()
        return

    from workers.heavy_toolpath import run_heavy_toolpath
    from workers.ai_depth import run_ai_depth
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return
        try:
            job.status = JobStatus.PROCESSING
            db.commit()
            if job.job_type == JobType.HEAVY_TOOLPATH:
                run_heavy_toolpath(job, db)
            elif job.job_type == JobType.AI_DEPTH:
                run_ai_depth(job, db)
            job.status = JobStatus.DONE
            job.progress = 100
            job.completed_at = datetime.utcnow()
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)[:500]
        db.commit()
    finally:
        db.close()


# ===========================================================================
# BILLING ROUTES (Phase 5 — Stripe integration stubs)
# ===========================================================================
@app.post("/billing/create-checkout")
def create_checkout(
    plan: PlanTier,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe Checkout session for upgrading to Pro/Team.
    Returns the checkout URL — frontend redirects the user there."""
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")

    prices = {
        PlanTier.PRO: os.getenv("STRIPE_PRO_PRICE_ID", "price_pro_placeholder"),
        PlanTier.TEAM: os.getenv("STRIPE_TEAM_PRICE_ID", "price_team_placeholder"),
    }
    if plan not in prices:
        raise HTTPException(400, "Invalid plan")

    # create or reuse Stripe customer
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email, metadata={"user_id": user.id})
        user.stripe_customer_id = customer.id
        db.commit()

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        line_items=[{"price": prices[plan], "quantity": 1}],
        mode="subscription",
        success_url=os.getenv("FRONTEND_URL", "http://localhost:3000") + "/billing/success",
        cancel_url=os.getenv("FRONTEND_URL", "http://localhost:3000") + "/billing/cancel",
        metadata={"user_id": user.id, "plan": plan.value},
    )
    return {"checkout_url": session.url}


@app.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (subscription created/cancelled/updated).
    Verifies webhook signature, then updates user plan in DB."""
    # S14 FIX: the parameter was `request` with no annotation, so FastAPI treated
    # it as a query parameter and never injected the Request - `await
    # request.body()` could not work and this endpoint was dead on arrival.
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception:
        raise HTTPException(400, "Invalid webhook signature")

    etype = event["type"]
    data = event["data"]["object"]
    db = SessionLocal()
    try:
        if etype == "checkout.session.completed":
            user_id = (data.get("metadata") or {}).get("user_id")
            plan = (data.get("metadata") or {}).get("plan", "pro")
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    user.plan = PlanTier(plan)
                    db.commit()

        # S15 FIX: only the upgrade event was handled, so a cancelled or expired
        # subscription left the account on Pro forever - paid features given away
        # indefinitely. Handle the downgrade side of the lifecycle too.
        elif etype in ("customer.subscription.deleted", "customer.subscription.updated"):
            status = data.get("status")
            customer_id = data.get("customer")
            if customer_id and (etype == "customer.subscription.deleted" or
                                status in ("canceled", "unpaid", "incomplete_expired")):
                user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
                if user and user.plan != PlanTier.FREE:
                    user.plan = PlanTier.FREE
                    db.commit()
    finally:
        db.close()

    return {"received": True}


# ===========================================================================
# HEALTH
# ===========================================================================
@app.get("/health")
def health():
    return {"status": "ok", "service": "relief-studio-api", "version": "1.0.0"}
