# Deploying EventLens to Vercel via Docker

EventLens is configured for container deployment on Vercel using `Dockerfile.vercel`.

---

## Deployment Steps

### Option A: Using Vercel CLI (Recommended)

1. Install the Vercel CLI if you haven't already:
   ```bash
   npm i -g vercel
   ```

2. Log in to Vercel:
   ```bash
   vercel login
   ```

3. Deploy from the `EventLens` directory:
   ```bash
   vercel
   ```

4. For production deployment:
   ```bash
   vercel --prod
   ```

---

### Option B: Deploying via GitHub / Git Repository

1. Push your code (including `Dockerfile.vercel` and `vercel.json`) to GitHub, GitLab, or Bitbucket.
2. Import your repository into [Vercel Dashboard](https://vercel.com/new).
3. Vercel will automatically detect `Dockerfile.vercel` at the root and deploy your application as an OCI container function.

---

## Required Environment Variables in Vercel Dashboard

When you create a **Vercel KV / Redis** store in the Vercel dashboard and link it to your project, Vercel automatically creates `KV_URL` (or `REDIS_URL`).

In your Vercel Project Settings -> **Environment Variables**, ensure the following are set:

| Key | Example / Value | Note |
|---|---|---|
| `REDIS_URL` or `KV_URL` | `rediss://default:password@host.upstash.io:6379` | Automatically linked by Vercel KV/Redis |
| `CELERY_TASK_ALWAYS_EAGER` | `True` | Recommended on Vercel to run tasks synchronously inside the web container |
| `DJANGO_SECRET_KEY` | `your-production-secret-key` | Random 50+ char secret |
| `DEBUG` | `False` | Recommended for production |
| `ALLOWED_HOSTS` | `.vercel.app,yourcustomdomain.com` | Allowed domains |
| `DATABASE_URL` | `postgres://user:password@host:5432/dbname` | Vercel Postgres / Neon / Supabase URL |
| `CLOUDINARY_CLOUD_NAME` | `your_cloud_name` | For photo upload storage |
| `CLOUDINARY_API_KEY` | `your_api_key` | For photo upload storage |
| `CLOUDINARY_API_SECRET` | `your_api_secret` | For photo upload storage |

---

## Frequently Asked Questions

### Is 30 MB Vercel Redis / Upstash memory enough?

**Yes! 30 MB is more than enough.**
- Celery task payloads store lightweight JSON metadata (e.g. `{"photo_id": 42}`). A single task message uses **< 1 KB**.
- Even with 500–1,000 tasks queued in Redis, total memory consumption is under **2 MB**.
- **Important**: Image files themselves are stored in Cloudinary/S3/Drive, not in Redis.
- Task results expire quickly or run in eager mode, keeping Redis memory footprint minimal.

### How do Celery tasks run on Vercel?

- **Option A (Inline Execution - Default)**: Set `CELERY_TASK_ALWAYS_EAGER=True`. Celery tasks run inline during the request within the Vercel Function container without needing a separate worker process.
- **Option B (Separate Worker)**: If you want heavy face-recognition background processing off the main HTTP request, host a Celery worker on a lightweight free instance (like Railway or Render) configured with the same `REDIS_URL`.
