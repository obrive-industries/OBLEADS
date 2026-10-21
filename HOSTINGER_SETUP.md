# Obrive Leads Engine — Hostinger VPS Deployment Guide

**Target Subdomain**: `obleads.obrive.com`  
**Application Port**: `5555` (Dashboard & Outbox Approval Desk)  
**Architecture**: Self-hosted autonomous AI SDR Engine with Brevo SMTP Relay & Free Cloud AI  

---

## 1. Architecture Overview

```
                      [ Internet / User Browser ]
                                  │
                                  ▼ (HTTPS:443 - SSL)
                       https://obleads.obrive.com
                                  │
                          [ Nginx Reverse Proxy ]
                          (HTTP Basic Auth / SSL)
                                  │
                                  ▼ (HTTP:5555)
                    [ Harvey Leads Engine Daemon ]
                     ├── Dashboard FastAPI (Port 5555)
                     ├── Heartbeat Background Runner (15m interval)
                     ├── SQLite WAL Database (data/harvey.db)
                     ├── Brevo SMTP Relay (Transactional Sending)
                     └── OpenRouter / Cloud AI (Poolside Laguna / Gemini)
```

---

## 2. Hostinger DNS Configuration

Log in to your **Hostinger hPanel** → **Domains** → **DNS / Nameservers**:

1. Add an **A Record**:
   - **Type**: `A`
   - **Name**: `obleads` (Points `obleads.obrive.com`)
   - **Points to**: `YOUR_HOSTINGER_VPS_IP` (e.g., `185.xxx.xxx.xxx`)
   - **TTL**: `300` (or `Default`)
2. Save changes and wait 5–10 minutes for DNS propagation.

---

## 3. Hostinger VPS Server Setup (Ubuntu 22.04 / 24.04)

SSH into your Hostinger VPS as `root` (or sudo user):
```bash
ssh root@YOUR_HOSTINGER_VPS_IP
```

### Step 3.1: System Packages & Dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx apache2-utils curl
```

### Step 3.2: Clone Repository on VPS
```bash
mkdir -p /var/www
cd /var/www
git clone https://github.com/shivansh-006/ob-leads.git leads
cd /var/www/leads
```

### Step 3.3: Set Up Python Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Step 3.4: Create `.env` Configuration
Create `/var/www/leads/.env`:
```bash
nano /var/www/leads/.env
```
Paste your production credentials:
```ini
# Brevo SMTP Relay (Outbound Sending)
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=bea186001@smtp-brevo.com
SMTP_PASSWORD=YOUR_BREVO_SMTP_KEY

# IMAP Settings (Inbound Reply Detection - Optional)
# If using Hostinger Business Email to receive replies:
IMAP_HOST=imap.hostinger.com
IMAP_PORT=993
IMAP_USERNAME=info@obrive.com
IMAP_PASSWORD=YOUR_HOSTINGER_EMAIL_PASSWORD
# (Note: If IMAP is blank, reply checking is safely skipped and outbound sending continues normally)

# AI Cloud Brain (Free Cloud API)
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GROQ_API_KEY=

# Lead Finder / Verification
REOON_API_KEY=YOUR_REOON_API_KEY
SERPER_API_KEY=
```
Save and exit (`Ctrl + O`, `Enter`, `Ctrl + X`). Secure file permissions:
```bash
chmod 600 /var/www/leads/.env
```

---

## 4. Run Continuously with Systemd Services

Create two lightweight systemd services so Harvey runs 24/7 and auto-restarts on server reboot.

### Service 1: Harvey Dashboard (`/etc/systemd/system/harvey-dashboard.service`)
```ini
[Unit]
Description=Obrive Leads Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/www/leads
EnvironmentFile=/var/www/leads/.env
ExecStart=/var/www/leads/.venv/bin/python3 -m harvey dashboard
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Service 2: Harvey Autonomous Heartbeat Runner (`/etc/systemd/system/harvey-runner.service`)
```ini
[Unit]
Description=Obrive Leads Heartbeat Agent
After=network.target harvey-dashboard.service

[Service]
Type=simple
User=root
WorkingDirectory=/var/www/leads
EnvironmentFile=/var/www/leads/.env
ExecStart=/var/www/leads/.venv/bin/python3 -m harvey run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Enable & Start Services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now harvey-dashboard
sudo systemctl enable --now harvey-runner
```

Check status:
```bash
sudo systemctl status harvey-dashboard
sudo systemctl status harvey-runner
```

---

## 5. Nginx Reverse Proxy with Password Protection & SSL

### Step 5.1: Create HTTP Basic Auth Password
Protect your approval desk so only authorized team members can access:
```bash
sudo htpasswd -c /etc/nginx/.ob_leads_passwd admin
# Enter a strong password when prompted
```

### Step 5.2: Nginx Configuration (`/etc/nginx/sites-available/obleads.conf`)
```bash
sudo nano /etc/nginx/sites-available/obleads.conf
```
Paste the following configuration:
```nginx
server {
    server_name obleads.obrive.com;

    # Basic Auth Protection
    auth_basic "Obrive Leads Executive Portal";
    auth_basic_user_file /etc/nginx/.ob_leads_passwd;

    # Reverse proxy to Harvey dashboard
    location / {
        proxy_pass http://127.0.0.1:5555;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 90;
    }

    # Custom log files
    access_log /var/log/nginx/obleads_access.log;
    error_log /var/log/nginx/obleads_error.log;
}
```

### Step 5.3: Enable Site & Test Configuration
```bash
sudo ln -s /etc/nginx/sites-available/obleads.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Step 5.4: Issue Free Let's Encrypt SSL Certificate
```bash
sudo certbot --nginx -d obleads.obrive.com --non-interactive --agree-tos -m info@obrive.com
```

Certbot will automatically configure HTTPS, HTTP-to-HTTPS redirect, and automatic certificate renewals!

---

## 6. Verification & Live Access

1. Open your browser and navigate to:
   👉 **`https://obleads.obrive.com`**
2. Enter your HTTP Basic Auth username (`admin`) and the password created in Step 5.1.
3. You will see the **Obrive Leads Command Desk**:
   - Outbox table with 1-click email approvals & "⚡ Send Approved Now" button.
   - Live analytics, prospect pipeline, and company enrichment status.
   - All outgoing emails automatically branded with Dr. Malakh Jibrail's executive signature and Obrive's AR/VR/MR branding.

---

## 7. Useful Management Commands

| Action | Command |
| :--- | :--- |
| **Check Dashboard Logs** | `journalctl -u harvey-dashboard -f` |
| **Check AI Heartbeat Logs** | `journalctl -u harvey-runner -f` |
| **Restart Leads Engine** | `sudo systemctl restart harvey-dashboard harvey-runner` |
| **View Pipeline Status** | `cd /var/www/leads && .venv/bin/harvey status` |
| **Update Code from Git** | `cd /var/www/leads && git pull && sudo systemctl restart harvey-dashboard harvey-runner` |
