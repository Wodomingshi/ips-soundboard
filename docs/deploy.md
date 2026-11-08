# Deploy your own private copy (open it from your phone)

Streamlit Community Cloud runs the dashboard online at a fixed address. Made private, only the
email addresses you allow can open it. Your holdings and IPS are stored as the app's secrets, so
nothing has to be uploaded from the phone.

What is stored where: the code comes from this public repository; your portfolio lives only in the
app's secrets on Streamlit's servers, visible to you as the app's owner. If you prefer not to store
it there, skip step 1 and use "Upload files" in the app instead.

## 1. Prepare your secrets (in your Codespace or on a computer with the repo)

```bash
git pull origin main
python tools/make_secrets.py private
```

This writes `private/streamlit-secrets.toml` (git-ignored). Open it and copy **all** of its content.

## 2. Create the app (about 5 minutes, easiest on a computer)

1. Go to [share.streamlit.io](https://share.streamlit.io) and **sign in with GitHub**. Allow access
   to your repositories when asked.
2. Click **Create app** → **Deploy a public app from GitHub** (it is made private in step 3).
3. Fill in:
   - **Repository:** `<your-github-name>/ips-soundboard`
   - **Branch:** `main`
   - **Main file path:** `app/dashboard.py`
   - **App URL:** pick a name you will recognise
4. Open **Advanced settings**:
   - **Python version:** 3.12
   - **Secrets:** paste the content you copied in step 1
   - **Save**
5. Click **Deploy**. The first start takes a few minutes (installing packages).

## 3. Make it private

1. In the app, open **Share** (top right).
2. Under who can view, choose **Only specific people can view this app**.
3. Add your email address (the one you will sign in with on your phone) and save.

## 4. Open it on your phone

1. Open the app's address in your phone's browser and sign in with the email you added.
2. Add it to your home screen: Safari → **Share** → **Add to Home Screen**; Chrome → **⋮** →
   **Add to Home screen**.
3. "My portfolio" is selected automatically. The sidebar (tap **»**) also offers the demo and uploads.

## Updating after a trade

Edit `private/holdings.csv` as usual, run `python tools/make_secrets.py private` again, and paste the
new content into the app: **your workspace at share.streamlit.io** → the app's **⋮** → **Settings** →
**Secrets** → **Save**. The app restarts with the new data within a minute.

## Good to know

- Apps that nobody opens for 12 hours go to sleep; the next visit wakes them in about 30 seconds.
- Prices come from Yahoo. If a price fails on the hosted app (cloud servers are sometimes
  throttled), the dashboard says which holdings could not be priced instead of breaking.
- The Claude steps (extract, classify, report) are not part of the hosted dashboard; they stay on
  the command line.
