#!/bin/sh
set -eu
# Nginx serves HTTP-01 challenges; Reality keeps TCP 443.
python3 - <<'PY'
import datetime,json,os,shutil
backup='/root/joysol-esa-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
os.mkdir(backup,0o700)
shutil.copy2('/etc/sing-box/config.json',backup+'/sing-box-config.json')
os.chmod(backup+'/sing-box-config.json',0o600)
nginx='/etc/nginx/conf.d/joysol-proxy.conf'
assert not os.path.exists(nginx),'Existing Joysol nginx site; inspect before overwriting'
with open(backup+'/manifest.json','w') as f:json.dump({'new_origin_port':8443,'existing_reality_port':443,'new_nginx_site':nginx},f,indent=2)
print('BACKUP_DIR='+backup)
os.makedirs('/var/lib/joysol-acme/.well-known/acme-challenge',mode=0o755,exist_ok=True)
with open('/var/lib/joysol-acme/.well-known/acme-challenge/joysol-origin-probe','w') as f:f.write('joysol-acme-origin-ready\n')
with open('/etc/nginx/conf.d/joysol-proxy.conf','w') as f:f.write('''server {
    listen 80;
    server_name proxy.example.com;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/joysol-acme;
        default_type text/plain;
        add_header Cache-Control "no-store" always;
    }
    location = /healthz { default_type text/plain; return 200 "joysol-origin-ready\\n"; }
    location / { return 404; }
}
''')
PY
install -d -m 700 /etc/nginx/joysol-bootstrap
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj /CN=proxy.example.com -addext subjectAltName=DNS:proxy.example.com -keyout /etc/nginx/joysol-bootstrap/key.pem -out /etc/nginx/joysol-bootstrap/cert.pem 2>/dev/null
chmod 600 /etc/nginx/joysol-bootstrap/key.pem
cat >> /etc/nginx/conf.d/joysol-proxy.conf <<'EOF'
server {
    listen 8443 ssl;
    server_name proxy.example.com;
    ssl_certificate /etc/nginx/joysol-bootstrap/cert.pem;
    ssl_certificate_key /etc/nginx/joysol-bootstrap/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/joysol-acme;
        default_type text/plain;
        add_header Cache-Control "no-store" always;
    }
    location = /healthz { default_type text/plain; return 200 "joysol-origin-ready\n"; }
    location / { return 404; }
}
EOF
nginx -t
systemctl enable --now nginx
systemctl reload nginx
curl --fail --silent --show-error --max-time 30 http://proxy.example.com/.well-known/acme-challenge/joysol-origin-probe
