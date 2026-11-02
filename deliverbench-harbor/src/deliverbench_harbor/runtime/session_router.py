"""Streaming Responses relay; inject sticky metadata without logging bodies/keys."""
import argparse
import hashlib
import http.client
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit


class Router(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,upstream):
        self.upstream=urlsplit(upstream.rstrip('/'))
        if self.upstream.scheme not in ('http','https') or not self.upstream.hostname:raise ValueError('Invalid upstream URL')
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def error(self,status,message):
        data=json.dumps({'error':message}).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/healthz':return self.error(200,'ok')
        if self.path.rstrip('/') not in ('/v1/models','/models'):return self.error(404,'not found')
        self.forward(None)
    def do_POST(self):
        if self.path.rstrip('/') not in ('/v1/responses','/responses'):return self.error(404,'Responses only')
        try:
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if not isinstance(body,dict):raise ValueError()
            metadata=body.get('metadata') or {}
            if not isinstance(metadata,dict):raise ValueError()
            session=self.headers.get('X-DeliverBench-Session-ID') or metadata.get('session_id') or body.get('prompt_cache_key')
            if not session:raise ValueError()
            session=str(session)
            if len(session)>128:session=hashlib.sha256(session.encode()).hexdigest()
            body['metadata']={**metadata,'session_id':session}
            encoded=json.dumps(body,ensure_ascii=False).encode()
        except (ValueError,TypeError):return self.error(400,'JSON object and sticky session identity required')
        self.forward(encoded)
    def forward(self,body):
        url=self.server.upstream
        cls=http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection
        conn=cls(url.hostname,url.port,timeout=600)
        suffix=self.path
        if url.path.endswith('/v1') and suffix.startswith('/v1/'):suffix=suffix[3:]
        drop={'host','content-length','connection','transfer-encoding','accept-encoding','x-deliverbench-session-id'}
        headers={k:v for k,v in self.headers.items() if k.lower() not in drop}
        headers['Accept-Encoding']='identity'
        if body is not None:headers['Content-Type']='application/json'
        started=False
        try:
            conn.request('POST' if body is not None else 'GET',url.path+suffix,body,headers)
            response=conn.getresponse();self.send_response(response.status);started=True
            for key,value in response.getheaders():
                if key.lower() not in ('connection','transfer-encoding','content-length','content-encoding'):
                    self.send_header(key,value)
            self.send_header('Connection','close');self.end_headers()
            while True:
                chunk=response.read1(65536)
                if not chunk:break
                self.wfile.write(chunk);self.wfile.flush()
        except Exception as exc:
            if not started:self.error(502,type(exc).__name__)
        finally:conn.close();self.close_connection=True


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--upstream',required=True)
    ap.add_argument('--bind',default='127.0.0.1');ap.add_argument('--port',type=int,default=18765)
    a=ap.parse_args();Router((a.bind,a.port),a.upstream).serve_forever()
