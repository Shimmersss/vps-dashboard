#!/bin/sh
set -eu
certbot certonly --webroot -w /var/lib/joysol-acme -d proxy.example.com --non-interactive --agree-tos --register-unsafely-without-email --cert-name proxy.example.com
python3 - <<'PY'
import json,os,pwd,secrets,subprocess,uuid
path='/etc/sing-box/config.json'
c=json.load(open(path));assert not any(i['tag']=='vless-esa-ws-in' for i in c['inbounds'])
uid=str(uuid.uuid4());ws_path='/connect/'+secrets.token_urlsafe(20)
c['inbounds'].append({'type':'vless','tag':'vless-esa-ws-in','listen':'127.0.0.1','listen_port':10000,'users':[{'name':'joysol-esa','uuid':uid}],'transport':{'type':'ws','path':ws_path}})
# Validate a candidate via stdin before changing the live file.
check=subprocess.run(['/usr/local/bin/sing-box','check','-c','/dev/stdin'],input=json.dumps(c),capture_output=True,text=True)
assert check.returncode==0,'Candidate sing-box config rejected'
fd=os.open(path,os.O_WRONLY|os.O_TRUNC)
with os.fdopen(fd,'w') as f:json.dump(c,f,indent=2)
fd=os.open('/root/joysol-esa-client.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump({'uuid':uid,'server':'proxy.example.com','port':443,'path':ws_path},f)
nginx='/etc/nginx/conf.d/joysol-proxy.conf'
s=open(nginx).read().replace('/etc/nginx/joysol-bootstrap/cert.pem','/etc/letsencrypt/live/proxy.example.com/fullchain.pem').replace('/etc/nginx/joysol-bootstrap/key.pem','/etc/letsencrypt/live/proxy.example.com/privkey.pem')
# Add WS location only to the TLS server; HTTP remains for certificate renewal.
marker='    listen 8443 ssl;'
start=s.index(marker)
insert=s.index('    location / { return 404; }',start)
s=s[:insert]+'''    location = '''+ws_path+''' {
        proxy_pass http://127.0.0.1:10000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
'''+s[insert:]
with open(nginx,'w') as f:f.write(s)
print('WS candidate checked; metadata stored privately.')
PY
nginx -t
/usr/local/bin/sing-box check -c /etc/sing-box/config.json
systemctl restart sing-box
systemctl reload nginx
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/joysol-nginx.sh <<'EOF'
#!/bin/sh
set -eu
/usr/sbin/nginx -t
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/joysol-nginx.sh
systemctl enable --now certbot.timer
systemctl is-active nginx sing-box
systemctl is-enabled nginx sing-box certbot.timer
openssl x509 -in /etc/letsencrypt/live/proxy.example.com/fullchain.pem -noout -subject -issuer -dates
curl --fail --silent --show-error --max-time 25 --resolve proxy.example.com:8443:127.0.0.1 https://proxy.example.com:8443/healthz
