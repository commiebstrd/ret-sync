#
# Copyright (C) 2016, Alexandre Gazet.
#
# Copyright (C) 2012-2014, Quarkslab.
#
# This file is part of ret-sync.
#
# ret-sync is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import os.path as altpath
import sys
import socket
import select
import base64
import binascii
import re
import traceback

try:
    from ret_sync.helpers import *
except ImportError:
    from helpers import *

try:
    import json
except:
    print("[-] failed to import json\n{:s}".format(repr(sys.exc_info())))
    sys.exit(0)

class Client():

    def __init__(self, s_client, s_srv, name):
        log.log("Client.__init__(name: {:s})".format(name))
        self.client_sock = s_client
        self.srv_sock = s_srv
        self.name = name
        self.enabled = False
        self.buffer = ''

    def close(self):
        log.log("Client.close(name: {:s})".format(self.name))
        self.enabled = False
        if self.client_sock:
            log.log("closing client socket", indent=1)
            self.client_sock.close()
        if self.srv_sock:
            log.log("closing server socket", indent=1)
            self.srv_sock.close()

    def feed(self, data):
        log.log("Client.feed(name: {:s}, data: {:s})".format(self.name, data))
        batch = []
        self.buffer = ''.join([self.buffer, data])
        if self.buffer.endswith("\n"):
            batch = [req for req in self.buffer.strip().split('\n') if req != '']
            self.buffer = ''
        log.log("batch: {:s}".format(batch), indent=1)
        return batch


class DispatcherSrv():

    def __init__(self):
        log.log("DispatcherSrv.__init__()")
        self.idb_clients = []
        self.dbg_client = None
        self.srv_socks = []
        self.opened_socks = []

        self.current_dbg = None
        self.current_dialect = 'unknown'
        self.current_idb = None
        self.current_module = None

        self.sync_mode_auto = True
        self.disconn_pat = re.compile('dbg disconnected')
        self.req_handlers = {
            'new_client': self.req_new_client,
            'new_dbg': self.req_new_dbg,
            'dbg_quit': self.req_dbg_quit,
            'idb_n': self.req_idb_n,
            'idb_list': self.req_idb_list,
            'module': self.req_module,
            'sync_mode': self.req_sync_mode,
            'cmd': self.req_cmd,
            'bc': self.req_bc,
            'kill': self.req_kill
        }


    def bind(self, host, port):
        log.log("DispatcherSrv.bind(host: {:s}, port: {})".format(host, port))
        self.dbg_srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.dbg_srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.dbg_srv_sock.bind((host, port))
        self.srv_socks.append(self.dbg_srv_sock)

        if not (socket.gethostbyname(host) == '127.0.0.1'):
            log.log("setting up localhost socket", indent=1)
            self.localhost_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.localhost_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.localhost_sock.bind(('localhost', port))
            self.srv_socks.append(self.localhost_sock)

    def accept(self, s):
        log.log("DispatcherSrv.accept()")
        new_socket, addr = s.accept()
        self.opened_socks.append(new_socket)

    def listen(self):
        log.log("DispatcherSrv.listen()")
        for s in self.srv_socks:
            s.listen(5)

    def close(self, s):
        log.log("DispatcherSrv.close()")
        s.close()
        self.opened_socks.remove(s)

    def loop(self):
        log.log("DispatcherSrv.loop()")
        self.listen()
        self.announcement("dispatcher listening")

        while True:
            rlist, wlist, xlist = select.select(self.srv_socks + self.opened_socks, [], [])

            if not rlist:
                msg = "socket error: select"
                self.announcement(msg)
                log.log(msg, indent=1)
                raise Exception("rabbit eating the cable")

            for s in rlist:
                if s in self.srv_socks:
                    self.accept(s)
                else:
                    self.handle(s)

    def handle(self, s):
        log.log("DispatcherSrv.handle(s: {:s})".format(s))
        client = self.sock_to_client(s)
        for req in self.recvall(client):
            self.parse_exec(s, req)

    # find client object for its srv socket
    def sock_to_client(self, s):
        log.log("DispatcherSrv.sock_to_client(s: {:s})".format(s))
        if self.current_dbg and (s == self.current_dbg.srv_sock):
            log.log("client == current_dbg", indent=1)
            client = self.current_dbg
        else:
            clist = [client for client in self.idb_clients if (client.srv_sock == s)]
            if not clist:
                log.log("new client", indent=1)
                client = Client(None, s, None)
                self.idb_clients.append(client)
            else:
                log.log("existing client", indent=1)
                client = clist[0]
        return client

    # buffered readline like function
    def recvall(self, client):
        log.log("DispatcherSrv.recvall(s: {:s})".format(client))
        try:
            data = client.srv_sock.recv(4096)
            if data == '':
                log.log("no data recv", indent=1)
                raise
        except:
            if client == self.current_dbg:
                msg = "debugger closed the connection"
                self.broadcast(msg)
                log.log(msg, indent=1)
                self.dbg_quit()
            else:
                self.client_quit(client.srv_sock)
                msg = "a client quit, clients(s) left: {:d}".format(len(self.idb_clients))
                self.broadcast(msg)
                log.log(msg, indent=1)
            log.log("returning empty list", indent=1)
            return []
        return client.feed(data)

    # parse and execute requests from clients (idbs or dbg)
    def parse_exec(self, s, req):
        log.log("DispatcherSrv.parse_exec(req: {:s})".format(req))
        if not (req[0:8] == '[notice]'):
            # this is a normal [sync] request from debugger, forward it
            self.forward(req)
            # receive 'dbg disconnected', socket can be closed
            if re.search(self.disconn_pat, req):
                log.log("got disconnect", indent=1)
                self.close(s)
            return

        req = self.normalize(req, 8)
        try:
            hash = json.loads(req)
        except:
            log.log("[-] dispatcher failed to parse json\n {:s}\n".format(req), indent=1)
            return

        type = hash['type']
        if not type in self.req_handlers:
            log.log("[*] dispatcher unknown request: {:s}".format(type), indent=1)
            return

        log.log("found type: {:s}".format(type), indent=1)
        req_handler = self.req_handlers[type]
        req_handler(s, hash)

    def normalize(self, req, taglen):
        log.log("DispatcherSrv.normalize(taglen: {}, req: {:s})".format(taglen, req))
        req = req[taglen:]
        req = req.replace("\\", "\\\\")
        req = req.replace("\n", "")
        log.log("normalized: {}".format(req), indent=1)
        return req

    def puts(self, msg, s):
        log.log("DispatcherSrv.puts(msg: {:s})".format(msg))
        s.sendall(msg)

    # dispatcher announcements are forwarded to the idb
    def announcement(self, msg, s=None):
        log.log("DispatcherSrv.announcement(msg: {:s})".format(msg))
        if not s:
            if not self.current_idb:
                log.log("not forwarding", indent=1)
                return
            s = self.current_idb.client_sock

        try:
            s.sendall('[notice]{{"type":"dispatcher","subtype":"msg","msg":"{:s}"}}\n'.format(msg))
        except:
            return

    # send message to all connected idb clients
    def broadcast(self, msg):
        log.log("DispatcherSrv.broadcast(msg: {:s})".format(msg))
        for idbc in self.idb_clients:
            self.announcement(msg, idbc.client_sock)

    # send dbg message to currently active idb client
    def forward(self, msg, s=None):
        log.log("DispatcherSrv.forward(msg: {:s})".format(msg))
        if not s:
            if not self.current_idb:
                log.log("not forwarding", indent=1)
                return
            s = self.current_idb.client_sock

        if s:
            s.sendall(msg + "\n")

    # send dbg message to all idb clients
    def forward_all(self, msg, s=None):
        log.log("DispatcherSrv.forward_all(msg: {:s})".format(msg))
        for idbc in self.idb_clients:
            self.forward(msg, idbc.client_sock)

    # disable current idb and enable new idb matched from current module name
    def switch_idb(self, new_idb):
        log.log("DispatcherSrv.switch_idb(idb: {:s})".format(new_idb))
        msg = '[sync]{{"type":"broker","subtype":"{:s}"}}\n'
        if (not self.current_idb == new_idb) & (self.current_idb.enabled):
            msg = msg.format("disable_idb")
            log.log("sending disable: {:s}".format(msg), indent=1)
            self.current_idb.client_sock.sendall(msg)
            self.current_idb.enabled = False

        if new_idb:
            msg = msg.format("enable_idb")
            log.log("sending enable: {:s}".format(msg), indent=1)
            new_idb.client_sock.sendall(msg)
            self.current_idb = new_idb
            new_idb.enabled = True

    # a new idb client connects to the dispatcher via its broker
    def req_new_client(self, srv_sock, hash):
        log.log("DispatcherSrv.req_new_client(hash: {:s})".format(str(hash)))
        port, name = hash['port'], hash['idb']
        try:
            log.log("conn localhost:{:d}".format(port), indent=1)
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', port))
            self.opened_socks.append(client_sock)
        except:
            log.log("failed conn".format(msg), indent=2)
            self.opened_socks.remove(srv_sock)
            srv_sock.close()
            return

        # check if an idb client is already registered with the same name
        conflicting = [client for client in self.idb_clients if (client.name == name)]

        # promote to idb client
        new_client = self.sock_to_client(srv_sock)
        new_client.client_sock = client_sock
        new_client.name = name
        self.broadcast("add new client (listening on port {:d}), nb client(s): {:d}".format(port, len(self.idb_clients)))

        if conflicting:
            self.broadcast("conflicting name: {:s} !".format(new_client.name))

        if not self.current_idb:
            log.log("setting new current_idb: {}".format(new_client), indent=1)
            self.current_idb = new_client

        # if new client match current module name, then enable it
        if self.current_module == name:
            log.log("switching to current module: {}".format(name), indent=1)
            self.switch_idb(new_client)

        # inform new client about debugger's dialect
        self.dbg_dialect(new_client)

    # clean state when a client is quiting
    def client_quit(self, s):
        log.log("DispatcherSrv.client_quit()")
        self.opened_socks.remove(s)
        # remove exiting client from the list of active clients
        for idbc in [idbc for idbc in self.idb_clients if (idbc.srv_sock == s)]:
            log.log("removing {}".format(idbc), indent=1)
            self.idb_clients.remove(idbc)
            self.opened_socks.remove(idbc.client_sock)
            idbc.close()

            # no more clients, let's kill ourself
            if not self.idb_clients:
                log.log("killing dispatcher".format(msg), indent=1)
                for s in self.srv_socks:
                    s.close()
                sys.exit()

    # determine if debugger is Windows specific
    def is_windows_dbg(self, dialect):
        log.log("DispatcherSrv.is_windows_dbg(dialect: {:s})".format(dialect))
        return (dialect in ['windbg', 'x64_dbg', 'ollydbg2'])

    # a new debugger client connects to the dispatcher
    def req_new_dbg(self, s, hash):
        log.log("DispatcherSrv.req_new_dbg(hash: {:s})".format(str(hash)))
        msg = hash['msg']
        if self.current_dbg:
            log.log("quiting", indent=1)
            self.dbg_quit()

        # promote to dbg client
        self.current_dbg = self.sock_to_client(s)
        self.current_dbg.client_sock = s
        self.idb_clients.remove(self.current_dbg)

        self.broadcast("new debugger client: {:s}".format(msg))

        # store dbb's dialect
        if 'dialect' in hash:
            self.current_dialect = hash['dialect']

            # case when IDA is on a linux/bsd host and connected to remote windows
            # use ntpath instead of posixpath
            if sys.platform.startswith('linux') or sys.platform == 'darwin':
                if self.is_windows_dbg(self.current_dialect):
                    log.log("switching to ntpath", indent=1)
                    global altpath
                    import ntpath as altpath

        self.dbg_dialect()

    # inform client about debugger's dialect
    def dbg_dialect(self, client=None):
        log.log("DispatcherSrv.dbg_dialect(client: {:s})".format(client))
        msg = '[sync]{{"type":"dialect","dialect":"{:s}"}}\n'.format(self.current_dialect)
        log.log("msg: {}".format(msg), indent=1)
        if client:
            client.client_sock.sendall(msg)
        else:
            for idbc in self.idb_clients:
                idbc.client_sock.sendall(msg)

    # debugger client disconnect from the dispatcher
    def req_dbg_quit(self, s, hash):
        log.log("DispatcherSrv.req_dbg_quit(hash: {:s})".format(str(hash)))
        msg = hash['msg']
        self.broadcast("debugger quit: {:s}".format(msg))
        self.dbg_quit()

    # clean state when debugger is quiting
    def dbg_quit(self):
        log.log("DispatcherSrv.dbg_quit()")
        self.opened_socks.remove(self.current_dbg.srv_sock)
        self.current_dbg.close()
        self.current_dbg = None
        self.current_module = None
        self.switch_idb(None)
        self.current_dialect = 'unknown'

    # handle kill notice from a client, exit properly if no more client
    def req_kill(self, s, hash):
        log.log("DispatcherSrv.req_kill(hash: {:s})".format(str(hash)))
        self.client_quit(s)
        self.broadcast("received a kill notice from client, {:d} client(s) left".format(len(self.idb_clients)))

    # send list of currently connected idb clients
    def req_idb_list(self, s, hash):
        log.log("DispatcherSrv.req_idb_list(hash: {:s})".format(str(hash)))
        clist = "> currently connected idb(s):\n"
        if not self.idb_clients:
            clist += "    no idb client yet\n"
        else:
            for i in range(len(self.idb_clients)):
                clist += ("    [{:d}] {:s}\n".format(i, self.idb_clients[i].name))
        log.log("sending: {:s}".format(clist), indent=1)
        s.sendall(clist)

    # manually set current active idb to idb n from idb list
    def req_idb_n(self, s, hash):
        log.log("DispatcherSrv.req_idb_n(hash: {:s})".format(str(hash)))
        idb = hash['idb']
        try:
            idbn = int(idb)
        except:
            s.sendall("> n should be a decimal value")
            return

        try:
            idbc = self.idb_clients[idbn]
        except:
            s.sendall("> {:d} is invalid (see idblist)".format(idbn))
            return

        self.switch_idb(idbc)
        s.sendall("> current idb set to {:d}".format(idbn))

    # dbg notice that its current module has changed
    def req_module(self, s, hash):
        log.log("DispatcherSrv.req_module(hash: {})".format(str(hash)))
        modpath = hash['path']
        self.current_module = modname = altpath.basename(modpath)
        matching = [idbc for idbc in self.idb_clients if (idbc.name.lower() == modname.lower())]

        if not self.sync_mode_auto:
            msg = "sync_mode_auto off"
            self.broadcast(msg)
            log.log(msg, indent=1)
            return

        if len(matching) == 1:
            # matched is set as active
            log.log("switching to {}".format(matching[0]), indent=1)
            self.switch_idb(matching[0])
        else:
            if not len(matching):
                msg = "mod request has no match for {:s}"
            else:
                msg = "ambiguous mod request, too many matches for {:s}"
            msg = msg.format(modname)
            self.broadcast(msg)
            log.log(msg, indent=1)
            # no match current idb (if existing) is disabled
            if self.current_idb.enabled:
                self.switch_idb(None)

    # sync mode tells if idb switch is automatic or manual
    def req_sync_mode(self, s, hash):
        log.log("DispatcherSrv.req_sync_mode(hash: {:s})".format(str(hash)))
        mode = hash['auto']
        self.broadcast("sync mode auto set to {:s}".format(mode))
        self.sync_mode_auto = (mode == "on")

    # bc request should be forwarded to all idbs
    def req_bc(self, s, hash):
        log.log("DispatcherSrv.req_bc(hash: {:s})".format(str(hash)))
        msg = "[sync]{:s}".format(json.dumps(hash))
        self.forward_all(msg)

    def req_cmd(self, s, hash):
        log.log("DispatcherSrv.req_cmd(hash: {:s})".format(str(hash)))
        cmd = hash['cmd']
        self.current_dbg.client_sock.sendall("{:s}\n".format(cmd))


if __name__ == "__main__":
    log = Logger(__file__, enable=False)
    server = DispatcherSrv()

    config = get_config()
    PORT = config.getint("INTERFACE", 'port')
    HOST = config.get("INTERFACE", 'host')
    server.announcement("configuration file loaded")

    try:
        server.bind(HOST, PORT)
    except Exception as e:
        log.log("dispatcher failed to bind on {:s}:{:s}\n-> {:s}".format(HOST, PORT, repr(e)))
        sys.exit()

    try:
        server.loop()
    except Exception as e:
        log.log("dispatcher failed\n-> {:s}".format(repr(e)))
        server.announcement("dispatcher stop")
