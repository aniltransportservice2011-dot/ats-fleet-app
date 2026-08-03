# Deploying Your Fleet App to the Cloud — Full Guide

This turns your local app into something your staff can open from any phone
or computer, anywhere, using a normal web address. No Python, no code
knowledge needed on their end — just a browser.

**Cost: about $6/month (~₹500/month), billed by the hosting company, ongoing.**
Not a one-time fee. I want that clear before you start.

---

## Part 1 — Create the server (15 minutes)

1. Go to **digitalocean.com** and sign up (email + payment method)
2. Once logged in, click **"Create" → "Droplets"**
3. Choose these settings:
   - **Image:** Ubuntu 22.04 (LTS)
   - **Plan:** Basic → Regular → the **$6/month** option (1GB RAM is enough)
   - **Region:** pick whichever is closest to India (e.g., Bangalore, if available — otherwise Singapore)
   - **Authentication:** choose **Password**, and set a strong password (write it down safely)
4. Click **"Create Droplet"**
5. Wait about a minute — you'll be given an **IP address** that looks like `164.90.123.45`. **Save this — it's your server's address.**

---

## Part 2 — Connect to your server

**On Windows:** Download and open **PuTTY** (putty.org), enter your server's IP address, click Open, then log in as `root` with the password you set.

**On Mac:** Open **Terminal**, and type (replacing with your real IP):
```
ssh root@164.90.123.45
```
Type "yes" if asked, then enter your password.

You're now controlling your server directly. Every command below goes here, not on your own computer.

---

## Part 3 — Install what the app needs (copy-paste these, one at a time)

```
apt update
apt install -y python3 python3-pip python3-venv unzip
```

---

## Part 4 — Get your app onto the server

**Easiest way: upload the zip file directly.**

On your **own computer** (not the server), open a terminal/command prompt where your `fleet_local_app.zip` is saved, and run (replace the IP):

```
scp fleet_local_app.zip root@164.90.123.45:/root/
```

Enter your server password when asked. Then **switch back to your server terminal** (from Part 2) and run:

```
cd /root
unzip fleet_local_app.zip -d fleet_app
cd fleet_app
```

---

## Part 5 — Set up the app on the server

Still on the server:

```
python3 -m venv venv
source venv/bin/activate
pip install flask gunicorn
```

This creates an isolated space for the app's dependencies, and installs
**gunicorn** — a production-grade way of running the app (the version we
used on your computer isn't meant to stay running permanently).

---

## Part 6 — Make it start automatically and stay running forever

Create a new file that tells the server to run your app permanently, even
after restarts. Run this command exactly as written:

```
nano /etc/systemd/system/fleetapp.service
```

This opens a text editor. Paste this in exactly:

```
[Unit]
Description=Fleet App
After=network.target

[Service]
WorkingDirectory=/root/fleet_app
ExecStart=/root/fleet_app/venv/bin/gunicorn -w 2 -b 0.0.0.0:80 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Press **Ctrl+O**, then **Enter** to save, then **Ctrl+X** to exit.

Now start it:

```
systemctl daemon-reload
systemctl enable fleetapp
systemctl start fleetapp
```

---

## Part 7 — Open the firewall so people can actually reach it

```
ufw allow 22
ufw allow 80
ufw enable
```
(Type `y` if it asks to confirm.)

---

## Part 8 — Test it

Open a browser — any computer, your phone, doesn't matter — and go to:

```
http://164.90.123.45
```

(using your actual server IP). You should see your app's home page, exactly
like it looked on your own computer.

**This is the address you give your staff.** They just open it in any
browser, on any device — nothing to install.

---

## Part 9 — Checking on it later / restarting if needed

If you ever need to check if it's running, or restart it after making
changes:

```
systemctl status fleetapp    (check if it's running)
systemctl restart fleetapp   (restart it)
```

---

## A few honest things to know

- **The IP address is a bit ugly to share** (`http://164.90.123.45`). If you
  want a real name instead (like `fleet.anitransport.com`), that needs a
  separate domain name purchase (~₹700/year) and a few more setup steps —
  tell me if you want that and I'll write that guide too.
- **No login/password protection yet** — right now, anyone with the link can
  open it. If your staff device could be lost or the link shared accidentally,
  this matters. I can add a simple login screen if you want that — just ask.
- **No automatic backups yet** — your data lives in one file on the server.
  I can set up a simple daily backup that emails or saves a copy elsewhere,
  if you want that added.

**If you get stuck on any exact step, tell me exactly what you typed and
what message you got back, and I'll help you through it directly.**
