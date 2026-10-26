"""Run on the server with JOYSOL_TEST=1 and an isolated config/data directory."""
import json,unittest,time
from unittest.mock import patch
import app as a
import telemetry_pb2 as p
class DashboardTest(unittest.TestCase):
 def setUp(self):
  with a.db() as c:
   for table in ['connections','daily','sessions','failures','bans']:c.execute('DELETE FROM '+table)
  self.client=a.app.test_client()
 def batch(self,cid='a',kind=2,up=123,down=456,domain='example.com',inbound='esa',**kw):
  x=p.Connection(id=cid,source='8.8.8.8:50234',destination='1.1.1.1:443',domain=domain,network='tcp',protocol='tls',inbound=inbound,inboundType='vless',createdAt=int(time.time()*1000),closedAt=int(time.time()*1000) if kind==2 else 0,uplinkTotal=up,downlinkTotal=down)
  return p.ConnectionEvents(events=[p.ConnectionEvent(type=kind,id=cid,connection=x)],**kw)
 def login(self):
  r=self.client.post('/monitor/login',data={'username':'test','password':'test-password','remember':'on'},headers={'Origin':'https://proxy.example.com'})
  self.assertEqual(r.status_code,302)
  self.assertIn('Secure',r.headers['Set-Cookie']);self.assertIn('HttpOnly',r.headers['Set-Cookie']);self.assertIn('Max-Age=2592000',r.headers['Set-Cookie'])
  return self.client.get('/monitor/api/data').get_json()['csrf']
 def test_closed_short_connection_and_replay(self):
  a.ingest(self.batch());a.ingest(self.batch(reset=True))
  with a.db() as c:
   r=c.execute('select * from connections').fetchone();self.assertEqual((r['upload'],r['download'],r['active']),(123,456,0))
   self.assertEqual(c.execute('select sum(upload+download) from daily').fetchone()[0],579)
 def test_delta_then_final_totals(self):
  a.ingest(self.batch(kind=0,up=0,down=0))
  a.ingest(p.ConnectionEvents(events=[p.ConnectionEvent(type=1,id='a',uplinkDelta=10,downlinkDelta=20)]))
  a.ingest(self.batch(up=30,down=50))
  with a.db() as c:self.assertEqual(c.execute('select sum(upload+download) from daily').fetchone()[0],80)
 def test_merge_exact_keys(self):
  a.ingest(self.batch(cid='a'));a.ingest(self.batch(cid='b'));a.ingest(self.batch(cid='c',inbound='reality'))
  self.login();d=self.client.get('/monitor/api/data').get_json()
  self.assertEqual(len(d['history']),2);self.assertEqual(sum(r['connections'] for r in d['history']),3)
  self.assertEqual(next(r for r in d['history'] if r['inbound']=='vless/esa')['download'],912)
 def test_auth_csrf_and_bans(self):
  self.assertEqual(self.client.get('/monitor/api/data').status_code,401)
  token=self.login()
  self.assertEqual(self.client.post('/monitor/api/ban',json={'ip':'8.8.8.8','action':'ban'}).status_code,403)
  headers={'Origin':'https://proxy.example.com','X-CSRF-Token':token}
  with patch.object(a,'firewall') as f:
   r=self.client.post('/monitor/api/ban',json={'ip':'8.8.8.8','action':'ban'},headers=headers);self.assertEqual(r.status_code,200);f.assert_called_with('8.8.8.8',True)
   r=self.client.post('/monitor/api/ban',json={'ip':'127.0.0.1','action':'ban'},headers=headers);self.assertEqual(r.status_code,400)
   r=self.client.post('/monitor/api/ban',json={'ip':'8.8.8.8;touch /tmp/no','action':'ban'},headers=headers);self.assertEqual(r.status_code,400)
  origin={'X-Origin-Guard':a.CONFIG['origin_guard'],'X-Origin-XFF':'9.9.9.9, 8.8.8.8'}
  self.assertEqual(self.client.get('/internal/gate',headers=origin).status_code,403)
  with patch.object(a,'firewall'):
   self.client.post('/monitor/api/ban',json={'ip':'8.8.8.8','action':'unban'},headers=headers)
  r=self.client.get('/internal/gate',headers=origin);self.assertEqual(r.status_code,204);self.assertEqual(r.headers['X-Verified-Client-IP'],'8.8.8.8')
  self.assertEqual(self.client.get('/internal/gate',headers={'X-Origin-XFF':'8.8.8.8'}).status_code,403)
  self.assertEqual(self.client.get('/internal/gate',headers=origin,environ_base={'REMOTE_ADDR':'8.8.8.8'}).status_code,403)
  self.assertEqual(self.client.post('/monitor/logout',json={},headers=headers).status_code,200)
  self.assertEqual(self.client.get('/monitor/api/data').status_code,401)
 def test_daily_per_ip_keeps_dates_and_global_summary(self):
  with a.db() as c:
   c.executemany('INSERT INTO daily VALUES(?,?,?,?)',[
    ('2026-10-15','8.8.8.8',10,20),('2026-10-15','1.1.1.1',30,40),
    ('2026-10-16','8.8.8.8',50,60)])
  self.login();d=self.client.get('/monitor/api/data?ip=8.8.8.8').get_json()
  self.assertEqual(len(d['daily_ips']),3)
  rows={(r['day'],r['ip']):r for r in d['daily_ips']}
  self.assertEqual(rows[('2026-10-15','8.8.8.8')]['download'],20)
  self.assertEqual(rows[('2026-10-16','8.8.8.8')]['upload'],50)
  self.assertEqual(next(r for r in d['daily'] if r['day']=='2026-10-15')['upload'],40)
  self.assertEqual(sum(r['download'] for r in d['daily_ips']),sum(r['download'] for r in d['daily']))
  self.assertIn('location',d['daily_ips'][0]);self.assertIn('isp',d['daily_ips'][0])
 def test_ipv6_source(self):
  self.assertEqual(a.source_ip('[2606:4700:4700::1111]:1234'),'2606:4700:4700::1111')
if __name__=='__main__':unittest.main()
