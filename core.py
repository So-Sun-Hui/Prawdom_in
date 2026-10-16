# SPDX-License-Identifier: GPL-2.0-or-later
"""Network and parsing code; standard library only, independent of QGIS."""
import hashlib
import html
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

FOOD = set('bar bbq cafe cafeteria fast_food ice_cream pub restaurant'.split())
SHOPS = set('bakery butcher chocolate coffee confectionery farm convenience greengrocer health_food ice_cream milk nuts pastry seafood spices tea wine honey'.split())
RESIDENTIAL = {'apartments', 'residential', 'house', 'dormitory', 'terrace', 'detached'}
CITY = 'https://prawdom.ru/k_city.php?d=99100&c=547099006&f='

class Cancelled(Exception):
    pass

class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts, self.links = [], []
        self.current = None
        self.ignored = 0
        self.feed(source)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.ignored += 1
        if tag == 'a':
            self.current = [attrs.get('href', ''), '']
        if tag in ('br', 'p', 'div', 'li', 'tr', 'td', 'title', 'h1', 'h2'):
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignored = max(0, self.ignored - 1)
        if tag == 'a' and self.current:
            self.links.append(tuple(self.current)); self.current = None
        if tag in ('p', 'div', 'li', 'tr', 'td', 'title', 'h1', 'h2'):
            self.parts.append('\n')
    def handle_data(self, data):
        if self.ignored:
            return
        self.parts.append(data)
        if self.current is not None:
            self.current[1] += data
    @property
    def text(self):
        return ''.join(self.parts).replace('\xa0', ' ').replace('\u202f', ' ')

def normalize_street(s):
    s = html.unescape(s).lower().replace('ё', 'е')
    s = re.sub(r'([0-9]+)-[аяй]+\b', r'\1', s)
    tokens = re.findall(r'[а-я0-9]+', s)
    ignore = set('улица ул площадь пл переулок пер проезд пр д проспект просп пр кт аллея ал бульвар бульв шоссе ш'.split())
    aliases = {'большая':'больш', 'большой':'больш', 'б':'больш', 'бол':'больш', 'малая':'мал', 'малый':'мал', 'мал':'мал', 'м':'мал', 'верхняя':'верх', 'верхний':'верх', 'верхн':'верх', 'нижняя':'ниж', 'нижний':'ниж', 'нижн':'ниж', 'первая':'1', 'первый':'1', 'вторая':'2', 'второй':'2', 'третья':'3', 'третий':'3'}
    return ' '.join(sorted(aliases.get(t, t) for t in tokens if t not in ignore))

def normalize_number(s):
    s = html.unescape(s).lower().replace('ё', 'е')
    for a, b in [('корпус','к'), ('корп.','к'), ('строение','с'), ('стр.','с'), ('стр','с')]:
        s = s.replace(a,b)
    return re.sub(r'[^а-я0-9]', '', s)

def prawdom_url(url):
    u = urllib.parse.urlsplit(urllib.parse.urljoin('https://prawdom.ru/', html.unescape(url.strip())))
    if u.hostname not in ('prawdom.ru', 'www.prawdom.ru') or u.path not in ('/dom.php', '/nsk.php', '/k_city.php'):
        raise ValueError('Нужна ссылка на карточку дома или улицу prawdom.ru.')
    return urllib.parse.urlunsplit(('https', 'prawdom.ru', u.path, urllib.parse.quote(u.query, safe='=&%/:,+', encoding='cp1251'), ''))

def decode_page(data):
    head = data[:3000].decode('ascii', errors='ignore')
    m = re.search(r'charset\s*=\s*["\']?([\w-]+)', head, re.I)
    codecs = [m.group(1)] if m else []
    for codec in codecs + ['utf-8-sig', 'cp1251']:
        try:
            return data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            pass
    raise ValueError('Не удалось определить кодировку страницы.')

def parse_house(source, url, fetched_at):
    doc = Document(source)
    match = re.search(r'(?:Площадь\s+жилых\s+помещений|Жилая\s+площадь)\s*[:\-–]?\s*([0-9][0-9\s]*(?:[.,][0-9]+)?)', doc.text, re.I)
    point = re.search(r'(?:[?&]|&amp;)ll=([0-9.]+)(?:,|%2C)([0-9.]+)', source, re.I)
    if not match:
        raise ValueError('В карточке нет жилой площади. Общая площадь не подставляется.')
    if not point:
        raise ValueError('В карточке нет координат дома.')
    area = float(re.sub(r'\s+', '', match.group(1)).replace(',', '.'))
    lon, lat = map(float, point.groups())
    if not (area > 0 and 1 <= lon <= 180 and 1 <= lat <= 90):
        raise ValueError('Некорректная площадь или координаты.')
    title = re.search(r'<title[^>]*>(.*?)</title>', source, re.I | re.S)
    name = re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', title.group(1)))).strip() if title else url
    return {'address': name, 'lon': lon, 'lat': lat, 'area': area, 'source': prawdom_url(url), 'fetched_at': fetched_at}

class Client:
    def __init__(self, cache, cancelled=lambda:False, interval=0.5):
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.cancelled, self.interval, self.last = cancelled, interval, 0
    def check(self):
        if self.cancelled():
            raise Cancelled('Импорт отменён.')
    def get(self, url, ttl=86400):
        self.check()
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + '.bin')
        if path.exists() and time.time() - path.stat().st_mtime < ttl:
            data, stamp = path.read_bytes(), path.stat().st_mtime
        else:
            while time.monotonic() - self.last < self.interval:
                self.check(); time.sleep(.05)
            req = urllib.request.Request(url, headers={'User-Agent':'Prawdom_in/1.4.4.7 (local QGIS importer)'})
            with urllib.request.urlopen(req, timeout=40) as response:
                target = urllib.parse.urlsplit(response.geturl())
                if target.scheme != 'https' or target.hostname not in ('prawdom.ru','www.prawdom.ru','api.openstreetmap.org'):
                    raise ValueError('Неожиданное перенаправление источника.')
                data = response.read(45 * 1024 * 1024 + 1)
            self.last = time.monotonic()
            if len(data) > 45*1024*1024:
                raise ValueError('Слишком большой ответ. Выберите меньшую область.')
            self.check(); path.write_bytes(data); stamp = path.stat().st_mtime
        return data, datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec='seconds')
    def page(self,url):
        data, stamp = self.get(prawdom_url(url), 7*86400)
        return decode_page(data), stamp

def osm_download(client, bbox, progress=lambda text:None):
    """Same adaptive OSM API download used for the previous projects."""
    elements = {}
    def tile(b, depth=0):
        client.check(); progress('OpenStreetMap: загрузка области…')
        url = 'https://api.openstreetmap.org/api/0.6/map?bbox=' + ','.join(f'{v:.7f}' for v in b)
        try:
            raw, _ = client.get(url)
        except urllib.error.HTTPError as ex:
            if ex.code not in (400, 413) or depth >= 5:
                raise
            x0,y0,x1,y1=b;xm=(x0+x1)/2;ym=(y0+y1)/2
            for sub in [(x0,y0,xm,ym),(xm,y0,x1,ym),(x0,ym,xm,y1),(xm,ym,x1,y1)]:tile(sub,depth+1)
            return
        root = ET.fromstring(raw)
        for item in root:
            if item.tag in ('node','way','relation'):
                elements[(item.tag,int(item.attrib['id']))] = item
    tile(bbox)
    nodes = {k[1]:(float(v.attrib['lon']),float(v.attrib['lat'])) for k,v in elements.items() if k[0]=='node'}
    ways = {k[1]:[int(n.attrib['ref']) for n in v.findall('nd')] for k,v in elements.items() if k[0]=='way'}
    result=[]
    for (kind, ident), el in elements.items():
        tags={t.attrib['k']:t.attrib['v'] for t in el.findall('tag')}
        if not tags:continue
        record={'type':kind,'id':ident,'tags':tags}
        if kind=='node':record['coords']=[nodes[ident]]
        elif kind=='way':record['coords']=[nodes[n] for n in ways[ident] if n in nodes]
        else:
            record['members']=[{'ref':int(m.attrib['ref']),'role':m.attrib.get('role',''),'coords':[nodes[n] for n in ways.get(int(m.attrib['ref']),[]) if n in nodes]} for m in el.findall('member') if m.attrib.get('type')=='way']
        result.append(record)
    return result

def address_links(client, elements, progress=lambda text:None):
    needed={}
    for e in elements:
        t=e['tags']
        if (t.get('building') in RESIDENTIAL or t.get('building:flats')) and t.get('addr:street') and t.get('addr:housenumber'):
            needed.setdefault(normalize_street(t['addr:street']),set()).add(normalize_number(t['addr:housenumber']))
    if not needed:return [], ['В OSM не найдены адреса жилых домов. Можно использовать ссылки на карточки.']
    streets={};errors=[]
    # Moscow directory, paged in increments of 150, identical source used before.
    for offset in range(0, 3300, 150):
        progress(f'Prawdom: поиск улиц ({len(streets)} из {len(needed)})…')
        source,_=client.page(CITY+str(offset))
        for url,label in Document(source).links:
            if 'nsk.php?nse=' in url and normalize_street(label) in needed:
                streets.setdefault(normalize_street(label),set()).add(prawdom_url(url))
        if len(streets)==len(needed):break
    links=set()
    for street,urls in streets.items():
        for url in sorted(urls):
            client.check();source,_=client.page(url)
            for target,_ in Document(source).links:
                if 'dom.php?hm=' not in target:continue
                full=prawdom_url(target);query=urllib.parse.parse_qs(urllib.parse.urlsplit(full).query,encoding='cp1251');raw=query.get('hm',[''])[0];house=raw.split('_',1)[-1]
                if normalize_number(house) in needed[street]:links.add(full)
    for street in sorted(set(needed)-set(streets)):
        errors.append('Улица не сопоставлена: '+street)
    if not links:errors.append('Карточки для адресов OSM не найдены. Используйте прямые ссылки.')
    return sorted(links),errors

def collect(client, urls, progress=lambda text:None):
    links=set();errors=[]
    for url in urls:
        url=prawdom_url(url)
        if '/dom.php?' in url:links.add(url)
        elif '/nsk.php?' in url:
            source,_=client.page(url)
            for target,_ in Document(source).links:
                if 'dom.php?hm=' in target:links.add(prawdom_url(target))
        else:errors.append('Для ручного ввода нужны карточки домов или страницы улиц: '+url)
    rows=[];seen=set()
    for i,url in enumerate(sorted(links)):
        client.check();progress(f'Prawdom: карточка {i+1} из {len(links)}')
        try:
            source,stamp=client.page(url);row=parse_house(source,url,stamp)
            key=(round(row['lon'],6),round(row['lat'],6),row['area'])
            if key not in seen:rows.append(row);seen.add(key)
        except Cancelled:raise
        except Exception as ex:errors.append(url+' — '+str(ex))
    return rows,errors
