#!/usr/bin/env python
# coding:utf-8

import os
import base64
import time
import re
import xlog
import thread
import simple_http_server
import urllib2
import urlparse


from config import config

default_pacfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.PAC_FILE)
user_pacfile = os.path.join(config.DATA_PATH, config.PAC_FILE)

current_path = os.path.dirname(os.path.abspath(__file__))
root_path = os.path.abspath( os.path.join(current_path, os.pardir, os.pardir))
data_root = os.path.join(root_path, 'data')

gae_proxy_listen = "GOAGENT_LISTEN"
pac_listen = "PAC_LISTEN"

def get_serving_pacfile():
    if not os.path.isfile(user_pacfile):
        serving_pacfile = default_pacfile
    else:
        serving_pacfile = user_pacfile
    return serving_pacfile

def get_opener():
    autoproxy = '127.0.0.1:8087'

    import ssl
    if getattr(ssl, "create_default_context", None):
        cafile = os.path.join(data_root, "gae_proxy", "CA.crt")
        context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH,
                                             cafile=cafile)
        https_handler = urllib2.HTTPSHandler(context=context)

        opener = urllib2.build_opener(urllib2.ProxyHandler({'http': autoproxy, 'https': autoproxy}), https_handler)
    else:
        opener = urllib2.build_opener(urllib2.ProxyHandler({'http': autoproxy, 'https': autoproxy}))
    return opener


class PacUtil(object):
    """GAEProxy Pac Util"""

    @staticmethod
    def update_pacfile(filename):
        opener = get_opener()

        listen_ip = config.LISTEN_IP
        autoproxy = gae_proxy_listen
        blackhole = pac_listen
        default = 'DIRECT'

        if config.PAC_ADBLOCK:
            try:
                xlog.info('try download %r to update_pacfile(%r)', config.PAC_ADBLOCK, filename)
                adblock_content = opener.open(config.PAC_ADBLOCK).read()
            except Exception as e:
                xlog.warn("pac_update download adblock fail:%r", e)
                return

        try:
            xlog.info('try download %r to update_pacfile(%r)', config.PAC_GFWLIST, filename)
            pac_content = opener.open(config.PAC_GFWLIST).read()
        except Exception as e:
            xlog.warn("pac_update download gfwlist fail:%r", e)
            return

        content = ''
        need_update = True
        with open(get_serving_pacfile(), 'rb') as fp:
            content = fp.read()

        try:
            placeholder = '// AUTO-GENERATED RULES, DO NOT MODIFY!'
            content = content[:content.index(placeholder)+len(placeholder)]
            content = re.sub(r'''blackhole\s*=\s*['"]PROXY [\.\w:]+['"]''', 'blackhole = \'PROXY %s\'' % blackhole, content)
            content = re.sub(r'''autoproxy\s*=\s*['"]PROXY [\.\w:]+['"]''', 'autoproxy = \'PROXY %s\'' % autoproxy, content)
            if content.startswith('//'):
                line = '// Proxy Auto-Config file generated by autoproxy2pac, %s\r\n' % time.strftime('%Y-%m-%d %H:%M:%S')
                content = line + '\r\n'.join(content.splitlines()[1:])
        except ValueError:
            need_update = False

        try:
            if config.PAC_ADBLOCK:
                xlog.info('%r downloaded, try convert it with adblock2pac', config.PAC_ADBLOCK)
                jsrule = PacUtil.adblock2pac(adblock_content, 'FindProxyForURLByAdblock', blackhole, default)
                content += '\r\n' + jsrule + '\r\n'
                xlog.info('%r downloaded and parsed', config.PAC_ADBLOCK)
            else:
                content += '\r\nfunction FindProxyForURLByAdblock(url, host) {return "DIRECT";}\r\n'
        except Exception as e:
            need_update = False
            xlog.exception('update_pacfile failed: %r', e)
            return

        try:
            autoproxy_content = base64.b64decode(pac_content)
            xlog.info('%r downloaded, try convert it with autoproxy2pac', config.PAC_GFWLIST)
            jsrule = PacUtil.autoproxy2pac(autoproxy_content, 'FindProxyForURLByAutoProxy', autoproxy, default)
            content += '\r\n' + jsrule + '\r\n'
            xlog.info('%r downloaded and parsed', config.PAC_GFWLIST)
        except Exception as e:
            need_update = False
            xlog.exception('update_pacfile failed: %r', e)
            return

        if need_update:
            with open(user_pacfile, 'wb') as fp:
                fp.write(content)
            xlog.info('%r successfully updated', user_pacfile)
            serving_pacfile = user_pacfile

    @staticmethod
    def autoproxy2pac(content, func_name='FindProxyForURLByAutoProxy', proxy=gae_proxy_listen, default='DIRECT', indent=4):
        """Autoproxy to Pac, based on https://github.com/iamamac/autoproxy2pac"""
        jsLines = []
        for line in content.splitlines()[1:]:
            if line and not line.startswith("!"):
                use_proxy = True
                if line.startswith("@@"):
                    line = line[2:]
                    use_proxy = False
                return_proxy = 'PROXY %s' % proxy if use_proxy else default
                if line.startswith('/') and line.endswith('/'):
                    jsLine = 'if (/%s/i.test(url)) return "%s";' % (line[1:-1], return_proxy)
                elif line.startswith('||'):
                    domain = line[2:].lstrip('.')
                    if len(jsLines) > 0 and ('host.indexOf(".%s") >= 0' % domain in jsLines[-1] or 'host.indexOf("%s") >= 0' % domain in jsLines[-1]):
                        jsLines.pop()
                    jsLine = 'if (dnsDomainIs(host, ".%s") || host == "%s") return "%s";' % (domain, domain, return_proxy)
                elif line.startswith('|'):
                    jsLine = 'if (url.indexOf("%s") == 0) return "%s";' % (line[1:], return_proxy)
                elif '*' in line:
                    jsLine = 'if (shExpMatch(url, "*%s*")) return "%s";' % (line.strip('*'), return_proxy)
                elif '/' not in line:
                    jsLine = 'if (host.indexOf("%s") >= 0) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url.indexOf("%s") >= 0) return "%s";' % (line, return_proxy)
                jsLine = ' ' * indent + jsLine
                if use_proxy:
                    jsLines.append(jsLine)
                else:
                    jsLines.insert(0, jsLine)
        function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
        return function

    @staticmethod
    def urlfilter2pac(content, func_name='FindProxyForURLByUrlfilter', proxy='127.0.0.1:8086', default='DIRECT', indent=4):
        """urlfilter.ini to Pac, based on https://github.com/iamamac/autoproxy2pac"""
        jsLines = []
        for line in content[content.index('[exclude]'):].splitlines()[1:]:
            if line and not line.startswith(';'):
                use_proxy = True
                if line.startswith("@@"):
                    line = line[2:]
                    use_proxy = False
                return_proxy = 'PROXY %s' % proxy if use_proxy else default
                if '*' in line:
                    jsLine = 'if (shExpMatch(url, "%s")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url == "%s") return "%s";' % (line, return_proxy)
                jsLine = ' ' * indent + jsLine
                if use_proxy:
                    jsLines.append(jsLine)
                else:
                    jsLines.insert(0, jsLine)
        function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
        return function

    @staticmethod
    def adblock2pac(content, func_name='FindProxyForURLByAdblock', proxy='127.0.0.1:8086', default='DIRECT', indent=4):
        """adblock list to Pac, based on https://github.com/iamamac/autoproxy2pac"""
        jsLines = []
        for line in content.splitlines()[1:]:
            if not line or line.startswith('!') or '##' in line or '#@#' in line:
                continue
            use_proxy = True
            use_start = False
            use_end = False
            use_domain = False
            use_postfix = []
            if '$' in line:
                posfixs = line.split('$')[-1].split(',')
                if any('domain' in x for x in posfixs):
                    continue
                if 'image' in posfixs:
                    use_postfix += ['.jpg', '.gif']
                elif 'script' in posfixs:
                    use_postfix += ['.js']
                else:
                    continue
            line = line.split('$')[0]
            if line.startswith("@@"):
                line = line[2:]
                use_proxy = False
            if '||' == line[:2]:
                line = line[2:]
                if '/' not in line:
                    use_domain = True
                else:
                    if not line.startswith('http://'):
                        line = 'http://' + line
                    use_start = True
            elif '|' == line[0]:
                line = line[1:]
                if not line.startswith('http://'):
                    line = 'http://' + line
                use_start = True
            if line[-1] in ('^', '|'):
                line = line[:-1]
                if not use_postfix:
                    use_end = True
            return_proxy = 'PROXY %s' % proxy if use_proxy else default
            line = line.replace('^', '*').strip('*')
            if use_start and use_end:
                if '*' in line:
                    jsLine = 'if (shExpMatch(url, "%s")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url == "%s") return "%s";' % (line, return_proxy)
            elif use_start:
                if '*' in line:
                    if use_postfix:
                        jsCondition = ' || '.join('shExpMatch(url, "%s*%s")' % (line, x) for x in use_postfix)
                        jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                    else:
                        jsLine = 'if (shExpMatch(url, "%s*")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (url.indexOf("%s") == 0) return "%s";' % (line, return_proxy)
            elif use_domain and use_end:
                if '*' in line:
                    jsLine = 'if (shExpMatch(host, "%s*")) return "%s";' % (line, return_proxy)
                else:
                    jsLine = 'if (host == "%s") return "%s";' % (line, return_proxy)
            elif use_domain:
                if line.split('/')[0].count('.') <= 1:
                    if use_postfix:
                        jsCondition = ' || '.join('shExpMatch(url, "http://*.%s*%s")' % (line, x) for x in use_postfix)
                        jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                    else:
                        jsLine = 'if (shExpMatch(url, "http://*.%s*")) return "%s";' % (line, return_proxy)
                else:
                    if '*' in line:
                        if use_postfix:
                            jsCondition = ' || '.join('shExpMatch(url, "http://%s*%s")' % (line, x) for x in use_postfix)
                            jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                        else:
                            jsLine = 'if (shExpMatch(url, "http://%s*")) return "%s";' % (line, return_proxy)
                    else:
                        if use_postfix:
                            jsCondition = ' || '.join('shExpMatch(url, "http://%s*%s")' % (line, x) for x in use_postfix)
                            jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                        else:
                            jsLine = 'if (url.indexOf("http://%s") == 0) return "%s";' % (line, return_proxy)
            else:
                if use_postfix:
                    jsCondition = ' || '.join('shExpMatch(url, "*%s*%s")' % (line, x) for x in use_postfix)
                    jsLine = 'if (%s) return "%s";' % (jsCondition, return_proxy)
                else:
                    jsLine = 'if (shExpMatch(url, "*%s*")) return "%s";' % (line, return_proxy)
            jsLine = ' ' * indent + jsLine
            if use_proxy:
                jsLines.append(jsLine)
            else:
                jsLines.insert(0, jsLine)
        function = 'function %s(url, host) {\r\n%s\r\n%sreturn "%s";\r\n}' % (func_name, '\n'.join(jsLines), ' '*indent, default)
        return function


class PACServerHandler(simple_http_server.HttpServerHandler):
    onepixel = b'GIF89a\x01\x00\x01\x00\x80\xff\x00\xc0\xc0\xc0\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'

    def address_string(self):
        return '%s:%s' % self.client_address[:2]

    def do_CONNECT(self):
        self.wfile.write(b'HTTP/1.1 403\r\nConnection: close\r\n\r\n')

    def do_GET(self):
        xlog.info('PAC from:%s %s %s ', self.address_string(), self.command, self.path)

        path = urlparse.urlparse(self.path).path # '/proxy.pac'
        filename = os.path.normpath('./' + path) # proxy.pac

        if self.path.startswith(('http://', 'https://')):
            data = b'HTTP/1.1 200\r\nCache-Control: max-age=86400\r\nExpires:Oct, 01 Aug 2100 00:00:00 GMT\r\nConnection: close\r\n'
            if filename.endswith(('.jpg', '.gif', '.jpeg', '.bmp')):
                data += b'Content-Type: image/gif\r\n\r\n' + self.onepixel
            else:
                data += b'\r\n This is the Pac server, not proxy port, use 8087 as proxy port.'
            self.wfile.write(data)
            xlog.info('%s "%s %s HTTP/1.1" 200 -', self.address_string(), self.command, self.path)
            return

        # check for '..', which will leak file
        if re.search(r'(\.{2})', self.path) is not None:
            self.wfile.write(b'HTTP/1.1 404\r\n\r\n')
            xlog.warn('%s %s %s haking', self.address_string(), self.command, self.path )
            return


        if filename != 'proxy.pac':
            xlog.warn("pac_server GET %s fail", filename)
            self.wfile.write(b'HTTP/1.1 404\r\n\r\n')
            return

        mimetype = 'text/plain'
        if self.path.endswith('.pac?flush') or time.time() - os.path.getmtime(get_serving_pacfile()) > config.PAC_EXPIRED:
            thread.start_new_thread(PacUtil.update_pacfile, (user_pacfile,))

        pac_filename = get_serving_pacfile()
        with open(pac_filename, 'rb') as fp:
            data = fp.read()

        host = self.headers.getheader('Host')
        host, _, port = host.rpartition(":")
        gae_proxy_proxy = host + ":" + str(config.LISTEN_PORT)
        pac_proxy = host + ":" + str(config.PAC_PORT)
        data = data.replace(gae_proxy_listen, gae_proxy_proxy)
        data = data.replace(pac_listen, pac_proxy)
        self.wfile.write(('HTTP/1.1 200\r\nContent-Type: %s\r\nContent-Length: %s\r\n\r\n' % (mimetype, len(data))).encode())
        self.wfile.write(data)

    def send_file(self, filename, mimetype):
        with open(filename, 'rb') as fp:
            data = fp.read()
        if data:
            self.wfile.write(('HTTP/1.1 200\r\nContent-Type: %s\r\nContent-Length: %s\r\n\r\n' % (mimetype, len(data))).encode())
            self.wfile.write(data)
