# How to Deploy StudyBuddy to Vercel with MySQL

This guide will help you deploy your Python Flask application to Vercel and connect it to a cloud MySQL database.

## Prerequisites

1.  **Vercel Account**: Sign up at [vercel.com](https://vercel.com).
2.  **Cloud MySQL Database**: Vercel does not host MySQL databases. You need a cloud provider.
    *   **Option A (Recommended for Free Tier)**: [Aiven](https://aiven.io/) (Free MySQL plan available).
    *   **Option B**: [PlanetScale](https://planetscale.com/) (Excellent, but free tier changes often).
    *   **Option C**: [Supabase](https://supabase.com/) (Postgres is default, but they have integrations).
    *   **Option D**: [Clever Cloud](https://www.clever-cloud.com/) (Has a free MySQL tier).

## Step 1: Set up your Cloud Database (Example with Aiven)

1.  Create an account on [Aiven.io](https://console.aiven.io/signup).
2.  Create a new **MySQL** service. Select the **Free** plan if available.
3.  Once running, get the **Service URI** or connection details:
    *   Host
    *   Port
    *   User
    *   Password
    *   Database Name (default is usually `defaultdb`, you can create `study_buddy3`).
4.  **Important**: You need to import your local database schema into this cloud database. You can use a tool like **MySQL Workbench** or **DBeaver** to connect to the cloud DB and run your SQL creation scripts.

## Step 2: Prepare Your Project

I have already updated your `app.py` to read from Environment Variables.
I have also updated `api/index.py` and `vercel.json` for deployment.

## Step 3: Deploy to Vercel

### Option A: Using Vercel CLI (Fastest)

1.  Open your terminal in VS Code.
2.  Install Vercel CLI:
    ```bash
    npm install -g vercel
    ```
3.  Login to Vercel:
    ```bash
    vercel login
    ```
4.  Deploy:
    ```bash
    vercel
    ```
5.  Follow the prompts:
    *   Set up and deploy? **Yes**
    *   Which scope? **(Select your account)**
    *   Link to existing project? **No**
    *   Project Name: **study-buddy**
    *   In which directory is your code located? **./** (Just press Enter)
    *   Want to modify these settings? **No**

### Option B: Using GitHub (Recommended for updates)

1.  Push your code to a GitHub repository.
2.  Go to the Vercel Dashboard and click **"Add New..."** -> **"Project"**.
3.  Import your GitHub repository.

## Step 4: Configure Environment Variables

**This is the most important step.** Your app will crash if you don't do this.

1.  Go to your Project Settings on Vercel.
2.  Go to **Environment Variables**.
3.  Add the following variables (copy values from your Cloud DB provider):

| Key | Value Example |
| :--- | :--- |
| `DB_HOST` | `mysql-service-account.aivencloud.com` |
| `DB_USER` | `avnadmin` |
| `DB_PASSWORD` | `your-secure-password` |
| `DB_NAME` | `study_buddy3` |
| `DB_PORT` | `12345` |
| `SECRET_KEY` | `generate-a-random-secret-string` |
| `GOOGLE_CLIENT_ID` | `your-google-client-id` |
| `GOOGLE_CLIENT_SECRET` | `your-google-client-secret` |

4.  **Redeploy**: After adding variables, you must redeploy for them to take effect. Go to the **Deployments** tab and click **Redeploy** on the latest commit.

## Important Notes

*   **File Uploads**: Vercel Serverless functions have a **read-only filesystem**. You cannot save files to `static/images` or `uploads/` permanently.
    *   *Current Behavior*: Uploads will appear to work but will disappear after a few minutes.
    *   *Solution*: Use a cloud storage service like **AWS S3**, **Cloudinary**, or **Vercel Blob** for storing user uploads.
*   **Google OAuth**: You must update your Google Cloud Console "Authorized Redirect URIs" to include your new Vercel domain:
    *   `https://your-project-name.vercel.app/google/auth`
    *   `https://your-project-name.vercel.app/google/auth/strict`
