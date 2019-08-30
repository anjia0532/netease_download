# -*- coding:utf-8 -*-
import re
import sys
import os
import random
import time
import json
import argparse
import requests
from hashlib import md5
from mutagen.id3 import ID3,TRCK,TIT2,TALB,TPE1,APIC,TDRC,COMM,TPOS,USLT
import html
import tqdm,base64, binascii
from Crypto.Cipher import AES


# 解析歌曲信息header头
headers = {
    'Accept': '*/*',
    'Accept-Encoding': 'gzip,deflate,sdch',
    'Accept-Language': 'zh-CN,zh;q=0.8,gl;q=0.6,zh-TW;q=0.4',
    'Connection': 'keep-alive',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Host': 'music.163.com',
    'Referer': 'http://music.163.com/search/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) ' \
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36'
}
# 下载时header
download_headers={
    'Accept-Encoding': 'identity;q=1, *;q=0',
    'DNT': '1',
    'Range': 'bytes=0-',
    'Referer': 'http://music.163.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) ' \
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36',
}
# 网易云音乐api 的uri
apis={
    'song_detail':'/weapi/v3/song/detail',
    'song_url':'/weapi/song/enhance/player/url',
    'play_detail':'/api/playlist/detail?id=%s',
    'album':'/weapi/v1/album/%s',
    'artist':'/weapi/v1/artist/%s',
    'artist_album':'/weapi/artist/albums/%s',
    'djradio':'/weapi/dj/program/byradio',
    'dj':'/weapi/dj/program/detail',
}

ss = requests.session()
ss.headers.update(headers)

s = u'\x1b[%d;%dm%s\x1b[0m'       # terminual color template

# 去除非法字符
def modificate_text(text):
    text = html.unescape(text or '')
    text = re.sub(r'//*', '-', text)
    text = text.replace('/', '-')
    text = text.replace('\\', '-')
    text = re.sub(r'\s\s+', ' ', text)
    return text

# 去除非法字符
def modificate_file_name_for_wget(file_name):
    file_name = re.sub(r'\s*:\s*', u' - ', file_name)
    file_name = file_name.replace('?', '')
    file_name = file_name.replace('"', '\'')
    file_name = file_name.replace('*', '_')
    return file_name

# 复制自 https://github.com/Jack-Cherish/python-spider/blob/01c82a70cdc1783a09d537023ef14947f8588533/Netease/Netease.py#L12
class encrypyed():
    """
    解密算法
    """
    def __init__(self):
        self.modulus = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b' \
            '725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda' \
            '92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3' \
            'e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
        self.nonce = '0CoJUm6Qyw8W8jud'
        self.pub_key = '010001'

    # 登录加密算法, 基于https://github.com/stkevintan/nw_musicbox脚本实现
    def encrypted_request(self, text):
        text = json.dumps(text)
        sec_key = self.create_secret_key(16)
        enc_text = self.aes_encrypt(self.aes_encrypt(text, self.nonce), sec_key.decode('utf-8'))
        enc_sec_key = self.rsa_encrpt(sec_key, self.pub_key, self.modulus)
        data = {'params': enc_text, 'encSecKey': enc_sec_key}
        return data

    def aes_encrypt(self, text, secKey):
        pad = 16 - len(text) % 16
        text = text + chr(pad) * pad
        encryptor = AES.new(secKey.encode('utf-8'), AES.MODE_CBC, b'0102030405060708')
        ciphertext = encryptor.encrypt(text.encode('utf-8'))
        ciphertext = base64.b64encode(ciphertext).decode('utf-8')
        return ciphertext

    def rsa_encrpt(self, text, pubKey, modulus):
        text = text[::-1]
        rs = pow(int(binascii.hexlify(text), 16), int(pubKey, 16), int(modulus, 16))
        return format(rs, 'x').zfill(256)

    def create_secret_key(self, size):
        return binascii.hexlify(os.urandom(size))[:16]

class neteaseMusic(object):
    def __init__(self):
        self.song_infos = []
        # 默认当前目录，可以通过参数指定
        self.dir_ = args.dir or os.getcwd()
        self.ep = encrypyed()
        self.timeout = 60


    def id_parser(self,key):
        '''
        获取所需id
        :key key
        :url url
        :return 所需id
        '''
        return re.search(r'%s.+?(\d+)'%key, args.url).group(1)

    def post_request(self, uri, params):
        '''
        调用网易api
        :uri api的uri，不含host段
        :params 待加密请求参数
        :return 如果code=200,返回执行结果，否则返回None
        '''
        data = self.ep.encrypted_request(params)
        resp = ss.post('http://music.163.com/%s'%uri, data=data, timeout=self.timeout)
        result = resp.json()
        if result['code'] != 200:
            print('post_request error')
            return None
        else:
            return result

    def get_durls(self,songs):
        '''
        :songs 待解析歌曲列表
        return {key 歌曲id,value 歌曲url}
        '''
        params = {'ids': list(s["id"] for s in songs), 'br': '320000', 'csrf_token': ''}
        result = self.post_request(apis['song_url'], params)
        return dict([str(s["id"]),s["url"]] for s in result['data'] if result['code'] == 200)


    def modified_id3(self, file_name, info):
        '''
        给歌曲增加id3 tag信息
        :file_name 待修改的歌曲完整地址
        :info 歌曲信息
        :return None
        '''

        if not os.path.exists(file_name):
            return None

        id3 = ID3()
        id3.add(TRCK(encoding=3, text=info['track']))
        id3.add(TDRC(encoding=3, text=info['year']))
        id3.add(TIT2(encoding=3, text=info['song_name']))
        id3.add(TALB(encoding=3, text=info['album_name']))
        id3.add(TPE1(encoding=3, text=info['artist_name']))
        id3.add(TPOS(encoding=3, text=info['cd_serial']))
        id3.add(COMM(encoding=3, desc=u'Comment', text=info['song_url']))
        id3.save(file_name)

    def get_song_infos(self, songs):
        '''
        获取歌曲列表中歌曲信息
        :songs 待解析歌曲列表
        :return 歌曲信息列表
        '''
        urls=self.get_durls(songs)
        for i in songs:
            song_info = self.get_song_info(i)
            song_info['durl']=urls.get(str(i['id'])) #歌曲地址
            self.song_infos.append(song_info)

    def get_song_info(self, i):
        '''
        解析歌曲信息
        :i song json
        :return 解析后的歌曲信息
        '''
        song_info = {}
        song_info['song_id'] = i['id']
        song_info['song_url'] = u'http://music.163.com/song/%s'% i['id']
        song_info['track'] = str(i['no']) #歌曲在专辑里的序号

        al,ar,h,m,publishTime='al','ar','h','m',i.get('publishTime')
        if not 'publishTime' in i:
            al,ar,h,m,publishTime='album','artists','hMusic','mMusic',i['album']['publishTime']
        song_info['mp3_quality'] =h in i and 'h' or m in i and 'm' or 'l'
        t = time.gmtime(int(publishTime<0 and 946656000000 or publishTime)*0.001)
        song_info['year'] = '-'.join([str(t.tm_year), str(t.tm_mon), str(t.tm_mday)])
        song_info['song_name'] = modificate_text(i['name']).strip()
        song_info['artist_name'] = modificate_text(' & '.join(str(ar['name']) for ar in i[ar]))
        song_info['album_pic_url'] = i[al]['picUrl']
        song_info['cd_serial'] = '1'
        song_info['album_name'] = modificate_text(i[al]['name'])

        file_name = song_info['song_name'] \
            + ' - ' + song_info['artist_name'] \
            + '.mp3'
        song_info['file_name'] = file_name
        return song_info

    def download_song(self,song_id,name=""):
        '''
        解析单曲
        :song_id 歌曲id
        :return 单曲信息
        '''
        params={'c':str([{'id':song_id}]),'ids':[song_id],'csrf_token':''}
        result = self.post_request(apis['song_detail'],params)
        song=result['songs'] and result['songs'][0]
        song["name"]=song["name"] or name
        self.get_song_infos([song])
        print(s % (2, 97, u'\n  >>  1首歌曲将要下载.'))

    def download_playlist(self,playlist_id):
        '''
        解析歌单
        :playlist_id 歌单id
        :return 歌单信息
        '''
        params={'id':playlist_id,'n':100000,'csrf_token':''}
        result = self.post_request(apis['play_detail']%playlist_id,params)
        songs = result['result']['tracks']
        d = modificate_text(result['result']['name'] + ' - ' \
            + result['result']['creator']['nickname'])
        self.dir_ = os.path.join(os.getcwd(), modificate_file_name_for_wget(d))
        self.amount_songs = str(len(songs))
        print(s % (2, 97, u'\n  >> ' + self.amount_songs + u' 首歌曲将要下载.'))
        self.get_song_infos(songs)


    def download_album(self,album_id):
        '''
        解析专辑
        :album_id 专辑id
        :return 专辑信息
        '''
        params={'csrf_token':''}
        result = self.post_request(apis['album']%album_id,params)
        songs=result['songs']
        d = modificate_text(result['album']['name'] \
            + ' - ' + result['album']['artist']['name'])
        self.dir_ = os.path.join(os.getcwd(), modificate_file_name_for_wget(d))
        self.amount_songs = str(len(songs))

        print(s % (2, 97, u'\n  >> ' + self.amount_songs + u' 首歌曲将要下载.'))

        for i in songs:
            i['publishTime']=result['album']['publishTime']
        self.get_song_infos(songs)


    def download_artist_albums(self,artist_id):
        '''
        解析艺术家所有专辑
        :artist_id 艺术家id
        :return 专辑id
        '''
        album_ids,offset,total=[],0,30
        while len(album_ids)<total:
            ids,total = self.get_artist_albums(artist_id,offset,30)
            album_ids.extend(ids)
            offset+=30
        for i in album_ids:
            self.download_album(i)

    def get_artist_albums(self,artist_id,offset=0,limit=30):
        '''
        分页艺术家专辑
        :artist_id 艺术家id
        :offset 偏移量 默认0
        :limit 每页专辑数量 默认30
        '''
        params={'offset':offset,'total':True,'limit':limit,'csrf_token':''}
        result = self.post_request(apis['artist_album']%artist_id,params)
        return list(al['id'] for al in result['hotAlbums'] if result['code']==200),result['artist']['albumSize']

    def download_artist_songs(self,artist_id):
        '''
        艺术家top 50单曲
        :artist_id 艺术家id
        :return top 50列表
        '''
        params={'csrf_token':''}
        result = self.post_request(apis['artist']%artist_id,params)
        songs=result['hotSongs']
        d = modificate_text(result['artist']['name'] + ' - ' + 'Top 50')
        self.dir_ = os.path.join(os.getcwd(), modificate_file_name_for_wget(d))
        amount_songs = str(len(songs))
        print(s % (2, 97, u'\n  >> ' + amount_songs + u' 首歌曲将要下载.'))
        for i in songs:
            i['publishTime']=result['artist']['publishTime']
        self.get_song_infos(songs)

    def download_djradio(self,djradio_id):
        '''
        电台节目
        :djradio_id 电台节目id
        :return 电台节目信息
        '''
        radio_ids,offset,total=[],0,30

        while len(radio_ids)<total:
            ids,total = self.get_djradios(djradio_id,offset,30)
            radio_ids.extend(ids)
            offset+=30
        for i in radio_ids:
            self.download_song(i["id"],i["name"])

    def get_djradios(self,djradio_id,offset=0,limit=30):
        '''
        分页电台节目
        :artist_id 艺术家id
        :offset 偏移量 默认0
        :limit 每页专辑数量 默认30
        '''
        params={'radioId':djradio_id,'csrf_token':'','limit':limit,'offset':offset}
        result = self.post_request(apis['djradio'],params)
        return list({"id":r['mainSong']['id'], "name":r['mainSong']['name']} for r in result['programs'] if result['code']==200),result['count']

    def download_dj(self,dj_id):
        '''
        解析dj
        :dj_id dj id
        :return dj信息
        '''
        params={'id':dj_id,'csrf_token':''}
        result = self.post_request(apis['dj'],params)
        self.download_song(result['program']['mainSong']['id'],result['program']['mainSong']['name'])

    def download(self, amount_songs=None, n=None):
        '''
        下载歌曲
        :amount_songs 下载几首
        :n 下第几首
        '''
        amount_songs=amount_songs or len(self.song_infos)

        dir_ = self.dir_
        if not os.path.exists(dir_):
            os.mkdir(dir_)

        ii = 1
        for i in self.song_infos:
            num = random.randint(0, 100) % 7
            col = s % (2, num + 90, i['file_name'])
            t = modificate_file_name_for_wget(i['file_name'])
            file_name = os.path.join(dir_, t)
            if os.path.exists(file_name) or not i['durl']:  # if file exists, no get_durl
                if not i['durl']:
                    print("暂无版权")
                if args.undownload:
                    self.modified_id3(file_name, i)
                ii += 1
                continue

            if not args.undownload:
                q = {'h': 'High', 'm': 'Middle', 'l': 'Low'}
                mp3_quality = str(q.get(i['mp3_quality']))

                print(u'\n  ++ 正在下载: #%s/%s# %s\n  ++ mp3_quality: %s' \
                    % (n or ii, amount_songs, col,s % (1, 91, mp3_quality)))

                file_name_for_wget = file_name.replace('`', '\`')

                r = requests.session().get(i['durl'], stream=True,headers=download_headers,timeout=self.timeout)

                length = int(r.headers.get('content-length'))

                with open(file_name_for_wget, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk:
                            f.write(chunk)

            self.modified_id3(file_name, i)
            ii += 1
            time.sleep(0)

    def url_parser(self,url):
        self.song_infos=[]
        print(url)
        if 'playlist' in url:
            print(s % (2, 92, u'\n  -- 正在分析歌单信息 ...'))
            self.download_playlist(self.id_parser('playlist'))
        elif 'toplist' in url:
            print(s % (2, 92, u'\n  -- 正在分析排行榜信息 ...'))
            self.download_playlist(self.id_parser('toplist') or '3779629')
        elif 'album' in url:
            print(s % (2, 92, u'\n  -- 正在分析专辑信息 ...'))
            self.download_album(self.id_parser('album'))
        elif 'artist' in url:
            artist_id = self.id_parser('artist')
            code = input('\n  >> 输入 a 下载该艺术家所有专辑.\n' \
                '  >> 输入 t 下载该艺术家 Top 50 歌曲.\n  >> ')
            if code == 'a':
                print(s % (2, 92, u'\n  -- 正在分析艺术家专辑信息 ...'))
                self.download_artist_albums(artist_id)
            elif code == 't':
                print(s % (2, 92, u'\n  -- 正在分析艺术家 Top 50 信息 ...'))
                self.download_artist_songs(artist_id)
            else:
                print(s % (1, 92, u'  --> Over'))
        elif 'song' in url:
            print(s % (2, 92, u'\n  -- 正在分析歌曲信息 ...'))
            self.download_song(self.id_parser('song'))
        elif 'djradio' in url:
            print(s % (2, 92, u'\n  -- 正在分析DJ节目信息 ...'))
            self.download_djradio(self.id_parser('id'))
        elif 'program' in url:
            print(s % (2, 92, u'\n  -- 正在分析DJ节目信息 ...'))
            self.download_dj(self.id_parser('id'))
        else:
            print(s % (2, 91, u'   请正确输入music.163.com网址.'))
            sys.exit(0)
        self.download()
        print('结束')

def main(url):
    x = neteaseMusic()
    x.url_parser(url)

if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='downloading any music.163.com')

    p.add_argument('url', help='any url of music.163.com')
    p.add_argument('-d','--dir', help='save files to this dir',default="mp3")
    p.add_argument('-c', '--undownload', action='store_true', \
        help='no download, using to renew id3 tags')
#     args = p.parse_args(args=["http://music.163.com/#/song?id=27836179"])
#     args = p.parse_args(args=["http://music.163.com/#/album?id=37253721"])
#     args = p.parse_args(args=["http://music.163.com/#/playlist?id=2225407480"])
#     args = p.parse_args(args=["http://music.163.com/#/discover/toplist?id=1978921795"])
#     args = p.parse_args(args=["http://music.163.com/#/artist?id=905705"])
#     args = p.parse_args(args=["http://music.163.com/#/djradio?id=526696677"])
#     args = p.parse_args(args=["http://music.163.com/#/program?id=1369232209"])
    args = p.parse_args()
    main(args.url)
