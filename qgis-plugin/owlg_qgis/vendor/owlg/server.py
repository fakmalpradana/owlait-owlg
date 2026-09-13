"""Server that publishes .owlgt: OGC API - Tiles, OGC API - Maps, and WMS 1.3.0.

The HTTP layer uses only the Python standard library, so it can run anywhere
without extra dependencies. Tiles are served straight out of the container (no
transcoding) whenever the requested format matches.
"""
import json, io, math, os, re, urllib.parse, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
from .tiles import open_owlgt, read_tile, TS, WORLD
from . import mosaic

TMS_ID = 'WebMercatorQuad'
CONFORMANCE = [
 "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core",
 "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/json",
 "http://www.opengis.net/spec/ogcapi-common-2/1.0/conf/collections",
 "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core",
 "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tileset",
 "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tilesets-list",
 "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/geodata-tilesets",
 "http://www.opengis.net/spec/ogcapi-maps-1/1.0/conf/core",
 "http://www.opengis.net/spec/ogcapi-maps-1/1.0/conf/scaling",
]

def _enc_png(rgb, alpha=None):
    from . import imgio as _io
    a = np.dstack([rgb, alpha]) if alpha is not None else rgb
    return _io.png_encode(np.ascontiguousarray(a), level=6), 'image/png'

def _enc_jpeg(rgb, q=85):
    from . import imgio as _io
    return _io.jpeg_encode(np.ascontiguousarray(rgb), level=q), 'image/jpeg'

def _enc_webp(rgb, q=80):
    from . import imgio as _io
    return _io.webp_encode(np.ascontiguousarray(rgb), level=q), 'image/webp'

def _pick_encoder(fmt):
    f = (fmt or '').lower()
    if 'jpeg' in f or 'jpg' in f: return lambda r, a: _enc_jpeg(r)
    if 'webp' in f: return lambda r, a: _enc_webp(r)
    return _enc_png

def _m2lat(y): return math.degrees(2*math.atan(math.exp(y*math.pi/WORLD)) - math.pi/2)
def _m2lon(x): return x*180.0/WORLD

class Source:
    def __init__(self, path, cid=None, title=None):
        self.path = path
        self.hdr, self.raw, self.base = open_owlgt(path)
        self.cid = cid or os.path.splitext(os.path.basename(path))[0]
        self.title = title or self.cid
        self.cache = {}
        self.lock = threading.Lock()
        b = self.hdr['bounds']
        self.bbox3857 = [b[0], b[1], b[2], b[3]]
        self.bbox4326 = [_m2lon(b[0]), _m2lat(b[1]), _m2lon(b[2]), _m2lat(b[3])]
    def tile(self, z, x, y):
        with self.lock:
            return read_tile(self.hdr, self.raw, self.base, z, x, y, self.cache)[0]
    def render(self, bbox, w, h, crs):
        with self.lock:
            return mosaic.render_bbox(self.hdr, self.raw, self.base, bbox, w, h, crs, self.cache)

def tms_doc(base_url):
    m = []
    for z in range(0, 25):
        m.append(dict(id=str(z), scaleDenominator=559082264.028717/(2**z),
                      cellSize=(2*WORLD/(TS*2**z)),
                      pointOfOrigin=[-WORLD, WORLD], tileWidth=TS, tileHeight=TS,
                      matrixWidth=2**z, matrixHeight=2**z))
    return dict(id=TMS_ID, title="Google Maps Compatible for the World",
                uri="http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad",
                crs="http://www.opengis.net/def/crs/EPSG/0/3857",
                orderedAxes=["E", "N"], tileMatrices=m)

class Handler(BaseHTTPRequestHandler):
    server_version = "owlg-ogcapi/1.0"
    def log_message(self, *a): pass
    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str): body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, indent=1), 'application/json')
    def _err(self, code, msg):
        self._json(dict(code=code, description=msg), code)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path.rstrip('/') or '/'
        q = {k.lower(): v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        srcs = self.server.sources
        root = f"http://{self.headers.get('Host', 'localhost')}"
        try:
            if p == '/' : return self._landing(root, srcs)
            if p == '/conformance': return self._json(dict(conformsTo=CONFORMANCE))
            if p == '/api': return self._json(self._openapi(root, srcs))
            if p == '/tileMatrixSets':
                return self._json(dict(tileMatrixSets=[dict(id=TMS_ID, title=TMS_ID,
                    links=[dict(rel='self', type='application/json', href=f'{root}/tileMatrixSets/{TMS_ID}')])]))
            if p == f'/tileMatrixSets/{TMS_ID}': return self._json(tms_doc(root))
            if p == '/collections':
                return self._json(dict(collections=[self._coll(root, s) for s in srcs.values()],
                                       links=[dict(rel='self', type='application/json', href=f'{root}/collections')]))
            m = re.match(r'^/collections/([^/]+)$', p)
            if m: return self._json(self._coll(root, self._src(srcs, m.group(1))))
            m = re.match(r'^/collections/([^/]+)/map/tiles$', p)
            if m:
                s = self._src(srcs, m.group(1))
                return self._json(dict(tilesets=[self._tileset(root, s)]))
            m = re.match(rf'^/collections/([^/]+)/map/tiles/{TMS_ID}$', p)
            if m: return self._json(self._tileset(root, self._src(srcs, m.group(1)), full=True))
            m = re.match(rf'^/collections/([^/]+)/map/tiles/{TMS_ID}/(\d+)/(\d+)/(\d+)(?:\.(\w+))?$', p)
            if m:   # OGC API - Tiles: {tileMatrix}/{tileRow}/{tileCol} = z/y/x
                cid, z, row, col, ext = m.groups()
                return self._tile(self._src(srcs, cid), int(z), int(col), int(row), ext or q.get('f'))
            m = re.match(r'^/collections/([^/]+)/map$', p)
            if m: return self._map(self._src(srcs, m.group(1)), q)
            m = re.match(r'^/xyz/([^/]+)/(\d+)/(\d+)/(\d+)(?:\.(\w+))?$', p)
            if m:   # XYZ convenience route: z/x/y, for Leaflet & MapLibre
                cid, z, x, y, ext = m.groups()
                return self._tile(self._src(srcs, cid), int(z), int(x), int(y), ext or q.get('f'))
            if p == '/wms': return self._wms(root, srcs, q)
            return self._err(404, f'not found: {p}')
        except KeyError as e:
            return self._err(404, str(e))
        except Exception as e:
            return self._err(500, f'{type(e).__name__}: {e}')

    def _src(self, srcs, cid):
        if cid not in srcs: raise KeyError(f'unknown collection: {cid}')
        return srcs[cid]

    def _landing(self, root, srcs):
        self._json(dict(title="OWLG tile service",
            description="Serves .owlgt: OGC API - Tiles, OGC API - Maps, and WMS 1.3.0",
            links=[
              dict(rel='self', type='application/json', href=f'{root}/'),
              dict(rel='conformance', type='application/json', href=f'{root}/conformance'),
              dict(rel='data', type='application/json', href=f'{root}/collections'),
              dict(rel='service-desc', type='application/json', href=f'{root}/api'),
              dict(rel='http://www.opengis.net/def/rel/ogc/1.0/tiling-schemes',
                   type='application/json', href=f'{root}/tileMatrixSets'),
              dict(rel='service', title='WMS 1.3.0 GetCapabilities',
                   href=f'{root}/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0'),
            ]))

    def _coll(self, root, s):
        return dict(id=s.cid, title=s.title, dataType='map',
            extent=dict(spatial=dict(bbox=[s.bbox4326], crs="http://www.opengis.net/def/crs/OGC/1.3/CRS84")),
            crs=["http://www.opengis.net/def/crs/EPSG/0/3857","http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
            links=[
              dict(rel='self', type='application/json', href=f'{root}/collections/{s.cid}'),
              dict(rel='http://www.opengis.net/def/rel/ogc/1.0/tilesets-map',
                   type='application/json', href=f'{root}/collections/{s.cid}/map/tiles'),
              dict(rel='http://www.opengis.net/def/rel/ogc/1.0/map',
                   type='image/png', href=f'{root}/collections/{s.cid}/map'),
              dict(rel='item', type='image/png', title='XYZ (Leaflet/MapLibre)',
                   href=f'{root}/xyz/{s.cid}/{{z}}/{{x}}/{{y}}.png', templated=True),
            ],
            owlgt=dict(profile=s.hdr['profile'], delta=s.hdr.get('delta'),
                       minzoom=s.hdr['minzoom'], maxzoom=s.hdr['maxzoom'],
                       tiles=len(s.hdr['index']),
                       guarantee=(f"|original-decoded| <= {s.hdr['delta']} DN per pixel"
                                  if s.hdr['profile'] == 'exact' else None)))

    def _tileset(self, root, s, full=False):
        d = dict(title=s.title, dataType='map', crs="http://www.opengis.net/def/crs/EPSG/0/3857",
            tileMatrixSetURI="http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad",
            links=[dict(rel='self', type='application/json',
                        href=f'{root}/collections/{s.cid}/map/tiles/{TMS_ID}'),
                   dict(rel='http://www.opengis.net/def/rel/ogc/1.0/tilematrixset',
                        type='application/json', href=f'{root}/tileMatrixSets/{TMS_ID}'),
                   dict(rel='item', type='image/png', templated=True,
                        href=f'{root}/collections/{s.cid}/map/tiles/{TMS_ID}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}.png')])
        if full:
            d['tileMatrixSetLimits'] = [
                dict(tileMatrix=str(z), **_limits(s, z))
                for z in range(s.hdr['minzoom'], s.hdr['maxzoom']+1)]
        return d

    def _tile(self, s, z, x, y, fmt):
        if not (s.hdr['minzoom'] <= z <= s.hdr['maxzoom']):
            return self._err(404, f'zoom {z} outside {s.hdr["minzoom"]}-{s.hdr["maxzoom"]}')
        t = s.tile(z, x, y)
        if t is None: return self._err(404, 'tile not present')
        enc = _pick_encoder(fmt or 'png')
        body, ct = enc(t[:, :, :3], None)
        self._send(200, body, ct, {'Cache-Control': 'public, max-age=86400'})

    def _map(self, s, q):
        bbox = [float(v) for v in q.get('bbox', ','.join(map(str, s.bbox4326))).split(',')]
        crs = q.get('crs', 'CRS:84')
        w = int(q.get('width', 512)); h = int(q.get('height', 512))
        if w*h > 36_000_000: return self._err(400, 'requested size too large')
        rgb, al = s.render(bbox, w, h, crs)
        enc = _pick_encoder(q.get('f', 'png'))
        body, ct = enc(rgb, al)
        self._send(200, body, ct)

    # ------------------------------- WMS 1.3.0 -------------------------------
    def _wms(self, root, srcs, q):
        req = (q.get('request') or '').lower()
        if req == 'getcapabilities': return self._wms_caps(root, srcs)
        if req == 'getmap': return self._wms_getmap(srcs, q)
        return self._wms_err('OperationNotSupported', f'REQUEST={req}')
    def _wms_err(self, code, msg):
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<ServiceExceptionReport version="1.3.0" xmlns="http://www.opengis.net/ogc">'
               f'<ServiceException code="{code}">{msg}</ServiceException></ServiceExceptionReport>')
        self._send(400, xml, 'text/xml')
    def _wms_caps(self, root, srcs):
        layers = []
        for s in srcs.values():
            b = s.bbox4326
            layers.append(f'''    <Layer queryable="0">
      <Name>{s.cid}</Name><Title>{s.title}</Title>
      <CRS>EPSG:3857</CRS><CRS>EPSG:4326</CRS><CRS>CRS:84</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>{b[0]:.8f}</westBoundLongitude><eastBoundLongitude>{b[2]:.8f}</eastBoundLongitude>
        <southBoundLatitude>{b[1]:.8f}</southBoundLatitude><northBoundLatitude>{b[3]:.8f}</northBoundLatitude>
      </EX_GeographicBoundingBox>
      <BoundingBox CRS="EPSG:3857" minx="{s.bbox3857[0]:.4f}" miny="{s.bbox3857[1]:.4f}" maxx="{s.bbox3857[2]:.4f}" maxy="{s.bbox3857[3]:.4f}"/>
      <BoundingBox CRS="EPSG:4326" minx="{b[1]:.8f}" miny="{b[0]:.8f}" maxx="{b[3]:.8f}" maxy="{b[2]:.8f}"/>
    </Layer>''')
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms"
  xmlns:xlink="http://www.w3.org/1999/xlink">
  <Service>
    <Name>WMS</Name><Title>OWLG WMS</Title>
    <Abstract>Serves .owlgt over WMS 1.3.0</Abstract>
    <OnlineResource xlink:href="{root}/wms"/>
    <MaxWidth>8192</MaxWidth><MaxHeight>8192</MaxHeight>
  </Service>
  <Capability>
    <Request>
      <GetCapabilities><Format>text/xml</Format>
        <DCPType><HTTP><Get><OnlineResource xlink:href="{root}/wms?"/></Get></HTTP></DCPType>
      </GetCapabilities>
      <GetMap><Format>image/png</Format><Format>image/jpeg</Format><Format>image/webp</Format>
        <DCPType><HTTP><Get><OnlineResource xlink:href="{root}/wms?"/></Get></HTTP></DCPType>
      </GetMap>
    </Request>
    <Exception><Format>XML</Format></Exception>
    <Layer>
      <Title>OWLG</Title><CRS>EPSG:3857</CRS><CRS>EPSG:4326</CRS><CRS>CRS:84</CRS>
{chr(10).join(layers)}
    </Layer>
  </Capability>
</WMS_Capabilities>'''
        self._send(200, xml, 'text/xml')
    def _wms_getmap(self, srcs, q):
        try:
            lay = (q.get('layers') or '').split(',')[0]
            s = self._src(srcs, lay)
            crs = (q.get('crs') or q.get('srs') or 'EPSG:3857').upper()
            bb = [float(v) for v in q['bbox'].split(',')]
            w = int(q['width']); h = int(q['height'])
        except Exception as e:
            return self._wms_err('MissingParameterValue', str(e))
        if w > 8192 or h > 8192: return self._wms_err('InvalidParameterValue', 'WIDTH/HEIGHT > 8192')
        # WMS 1.3.0: EPSG:4326 uses lat,lon axis order; CRS:84 uses lon,lat
        if crs == 'EPSG:4326': bb = [bb[1], bb[0], bb[3], bb[2]]
        rgb, al = s.render(bb, w, h, 'CRS:84' if crs in ('EPSG:4326', 'CRS:84') else crs)
        fmt = (q.get('format') or 'image/png')
        transparent = (q.get('transparent') or 'FALSE').upper() == 'TRUE'
        if 'png' in fmt and transparent: body, ct = _enc_png(rgb, al)
        else: body, ct = _pick_encoder(fmt)(rgb, None)
        self._send(200, body, ct)

    def _openapi(self, root, srcs):
        return dict(openapi="3.0.3", info=dict(title="OWLG tile service", version="1.0.0"),
                    servers=[dict(url=root)],
                    paths={"/collections": {}, "/collections/{collectionId}/map/tiles/"+TMS_ID+"/{tileMatrix}/{tileRow}/{tileCol}": {},
                           "/collections/{collectionId}/map": {}, "/wms": {}})

def _limits(s, z):
    xs = [int(k.split('/')[1]) for k in s.hdr['index'] if k.startswith(f'{z}/')]
    ys = [int(k.split('/')[2]) for k in s.hdr['index'] if k.startswith(f'{z}/')]
    if not xs: return dict(minTileRow=0, maxTileRow=0, minTileCol=0, maxTileCol=0)
    return dict(minTileRow=min(ys), maxTileRow=max(ys), minTileCol=min(xs), maxTileCol=max(xs))

def serve(paths, host='127.0.0.1', port=8080, block=True):
    srcs = {}
    for p in ([paths] if isinstance(paths, str) else paths):
        s = Source(p); srcs[s.cid] = s
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.sources = srcs
    url = f"http://{host}:{port}"
    print(f"OWLG serving {len(srcs)} collections at {url}")
    print(f"  OGC API landing : {url}/")
    print(f"  Collections     : {url}/collections")
    print(f"  OGC tiles       : {url}/collections/<id>/map/tiles/{TMS_ID}/{{z}}/{{y}}/{{x}}.png")
    print(f"  XYZ (Leaflet)   : {url}/xyz/<id>/{{z}}/{{x}}/{{y}}.png")
    print(f"  WMS 1.3.0       : {url}/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0")
    if not block:
        t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start(); return httpd
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    return httpd
