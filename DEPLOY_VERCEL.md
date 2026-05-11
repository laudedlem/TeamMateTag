# Deploying Teammate Tag to Vercel

This guide assumes:

- the code is in GitHub at `laudedlem/TeamMateTag`
- your Supabase database already exists
- your Supabase database already has the baseball tables loaded

This repo is already set up for Vercel:

- `vercel.json` routes all web requests into `api/index.py`
- `api/index.py` imports the real Flask app from `web/server.py`
- `requirements.txt` lists the Python packages Vercel needs

## 1. Check that your local `.env` has the real database connection

In the project folder there should be a file named `.env`.

Inside it, there should be a line that starts with:

```env
DATABASE_URL=postgresql://...
```

That value should be the full Supabase transaction pooler connection string,
including the password.

## 2. Push the latest local code to GitHub

You must do this before Vercel can deploy the fixed code.

Open the project folder in File Explorer.

Then click the address bar at the top of the window, type:

```text
powershell
```

and press Enter.

That opens a PowerShell terminal already inside the project folder.

Now run these commands one at a time:

```powershell
git add api/index.py requirements.txt README.md DEPLOY_VERCEL.md
git commit -m "Prepare Vercel deployment"
git push origin main
```

If `git commit` says there is nothing to commit, that is fine. Still run:

```powershell
git push origin main
```

## 3. Delete any broken old Vercel project

If you already created a Vercel project for this repo and it only showed 404s,
delete that Vercel project first so you start from a clean state.

In Vercel:

1. Open the project
2. Click `Settings`
3. Scroll to `Advanced`
4. Click `Delete Project`

This does not delete your GitHub repo or your Supabase database.

## 4. Create a fresh Vercel project

Go to:

<https://vercel.com/new>

Then:

1. Sign in with GitHub if needed
2. Find the `TeamMateTag` repository
3. Click `Import`

## 5. Use these Vercel settings

On the import screen:

- **Framework Preset:** `Other`
- **Root Directory:** leave it alone
- **Build Command:** leave blank
- **Output Directory:** leave blank
- **Install Command:** leave blank

Do not add any Vercel plugin.

## 6. Add the environment variable

Before clicking Deploy, open the `Environment Variables` section.

Add this:

- **Name:** `DATABASE_URL`
- **Value:** paste the full `DATABASE_URL` value from your local `.env`

Use the exact string. Do not edit the password encoding.

If the value in your `.env` looks like this:

```env
DATABASE_URL=postgresql://postgres.xxx:PASSWORD@aws-1-us-west-1.pooler.supabase.com:6543/postgres
```

then the Vercel value should be everything after the `=`.

## 7. Deploy

Now click `Deploy`.

Vercel will:

- install `flask`
- install `psycopg[binary]`
- install `python-dotenv`
- install `requests`
- build the Python serverless function from `api/index.py`

## 8. Test the deployed site

After the deploy succeeds, open the Vercel URL.

Check:

1. the home page loads
2. `Batting Practice` opens
3. `Film Review` opens
4. `Division Rivalry` opens
5. no 404 page appears
6. no 500 error appears

## 9. If it fails

If deployment fails, or the site opens but errors:

1. open the project in Vercel
2. click `Deployments`
3. click the newest deployment
4. open the logs
5. copy the first real error message

Then share:

- the Vercel URL
- whether the failure is a 404 or 500
- the first error line from the logs

That is enough to debug the next issue quickly.
