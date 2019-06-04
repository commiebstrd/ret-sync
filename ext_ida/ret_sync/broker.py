#
# Copyright (C) 2016, Alexandre Gazet.
#
# Copyright (C) 2012-2015, Quarkslab.
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

#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Note that broker.py is executed by IDA Pro so it is not possible to see
# any output using print() or similar

import os
import sys
import time
import re
import shlex
import argparse
import subprocess
import socket
import select
import binascii

try:
    import json
except:
    print("[-] failed to import json\n{:s}".format(repr(sys.exc_info())))
    sys.exit(0)

try:
    from ret_sync.helpers import *
except ImportError:
    from helpers import *

RUN_DISPATCHER_MAX_ATTEMPT = 4

# default value is current script's path (should be in includes)
DISPATCHER_PATH = os.path.join(os.path.realpath(os.path.dirname(__file__)), "dispatcher.py")
if not os.path.exists(DISPATCHER_PATH):
    print("[-] dispatcher path is not properly set, current value: <{:s}>".format(DISPATCHER_PATH))
    sys.exit(0)

class Client():

    def __init__(self, s):
        log.log("Client.__init__()")
        self.sock = s
        self.buffer = ''

    def feed(self, data):
        log.log("Client.feed()")
        batch = []
        self.buffer = ''.join([self.buffer, data])
        if self.buffer.endswith("\n"):
            batch = [req for req in self.buffer.strip().split('\n') if req != '']
            self.buffer = ''
        return batch


class BrokerSrv():

    def puts(self, msg):
        log.log("BrokerSrv.puts()")
        print(msg)
        sys.stdout.flush()

    def announcement(self, msg):
        log.log("BrokerSrv.announcement()")
        self.puts('[sync]{{"type":"broker","subtype":"msg","msg":"{:s}"}}\n'.format(msg))

    def notice_idb(self, msg):
        log.log("BrokerSrv.notic_idb()")
        self.puts('[sync]{{"type":"broker","subtype":"notice","port":"{:d}"}}\n'.format(msg))

    def notice_dispatcher(self, type, args=None):
        log.log("BrokerSrv.notice_dispatcher()")
        if args:
            notice = '[notice]{{"type":"{:s}",{:s}}}\n'.format(type, args)
        else:
            notice = '[notice]{{"type":"{:s}"}}\n'.format(type)

        self.notify_socket.sendall(notice)

    def bind(self):
        log.log("BrokerSrv.bind()")
        self.srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv_sock.bind(('localhost', 0))
        self.srv_port = self.srv_sock.getsockname()[1]

    def run_dispatcher(self):
        log.log("BrokerSrv.run_dispatcher()")
        cmdline = '"{:s}" -u "{:s}"'.format(os.path.join(PYTHON_PATH, PYTHON_BIN), DISPATCHER_PATH)
        tokenizer = shlex.shlex(cmdline)
        tokenizer.whitespace_split = True
        args = [arg.replace('"', '') for arg in list(tokenizer)]

        try:
            proc = subprocess.Popen(args, shell=False, close_fds=True)
            pid = proc.pid
        except:
            pid = None
            self.announcement("failed to run dispatcher")

        time.sleep(0.2)
        return pid

    def notify(self):
        log.log("BrokerSrv.notify()")
        for attempt in range(RUN_DISPATCHER_MAX_ATTEMPT):
            try:
                self.notify_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.notify_socket.connect((HOST, PORT))
                break
            except:
                self.notify_socket.close()
                if (attempt != 0):
                    self.announcement("failed to connect to dispatcher (attempt {:d})".format(attempt))
                if (attempt == (RUN_DISPATCHER_MAX_ATTEMPT - 1)):
                    self.announcement("failed to connect to dispatcher, too much attempts, exiting...")
                    sys.exit()

            self.announcement("dispatcher not found, trying to run it")
            pid = self.run_dispatcher()
            if pid:
                self.announcement("dispatcher now runs with pid: {:d}".format(pid))

        time.sleep(0.1)
        self.notice_dispatcher("new_client", '"port":{:d},"idb":"{:s}"'.format(self.srv_port, self.name))
        self.announcement('connected to dispatcher')
        self.notice_idb(self.srv_port)

    def accept(self):
        log.log("BrokerSrv.accept()")
        new_socket, addr = self.srv_sock.accept()
        self.clients_list.append(Client(new_socket))
        self.opened_sockets.append(new_socket)

    def close(self, s):
        log.log("BrokerSrv.close()")
        client = [client for client in self.clients_list if (client.sock == s)]
        if len(client) == 1:
            self.clients_list.remove(client[0])
        s.close()
        self.opened_sockets.remove(s)

    def recvall(self, client):
        log.log("BrokerSrv.recvall()")
        try:
            data = client.sock.recv(4096)
            if data == '':
                raise
        except:
            self.announcement("dispatcher connection error, quitting")
            sys.exit()

        return client.feed(data)

    def req_dispatcher(self, s, hash):
        log.log("BrokerSrv.req_dispatcher()")
        subtype = hash['subtype']
        if (subtype == 'msg'):
            msg = hash['msg']
            self.announcement("dispatcher msg: {:s}".format(msg))

    def req_cmd(self, s, hash):
        log.log("BrokerSrv.req_cmd()")
        cmd = hash['cmd']
        self.notice_dispatcher("cmd", '"cmd":"{:s}"'.format(cmd))

    def req_kill(self, s, hash):
        log.log("BrokerSrv.req_kill()")
        self.notice_dispatcher("kill")
        self.announcement("received kill notice")
        for s in ([self.srv_sock] + self.opened_sockets):
            s.close()
        sys.exit()

    def parse_exec(self, s, req):
        log.log("BrokerSrv.parse_exec()")
        if not (req[0:8] == '[notice]'):
            self.puts(req)
            return

        req = self.normalize(req, 8)

        try:
            hash = json.loads(req)
        except:
            log.log("[-] broker failed to parse json\n {:s}".format(repr(req)))
            return

        type = hash['type']
        if not type in self.req_handlers:
            log.log("[*] broker unknown request: {:s}".format(type))
            return

        req_handler = self.req_handlers[type]
        req_handler(s, hash)

    def normalize(self, req, taglen):
        log.log("BrokerSrv.normalize()")
        req = req[taglen:]
        req = req.replace("\\", "\\\\")
        req = req.replace("\n", "")
        return req

    def handle(self, s):
        log.log("BrokerSrv.handle()")
        client = [client for client in self.clients_list if (client.sock == s)]
        if len(client) == 1:
            batch = self.recvall(client[0])
        else:
            self.announcement("socket error")
            raise Exception("rabbit eating the cable")

        for req in batch:
            if req != '':
                self.parse_exec(s, req)

    def loop(self):
        log.log("BrokerSrv.loop()")
        self.srv_sock.listen(5)
        while True:
            rlist, wlist, xlist = select.select([self.srv_sock] + self.opened_sockets, [], [])

            if not rlist:
                self.announcement("socket error: select")
                raise Exception("rabbit eating the cable")

            for s in rlist:
                if s is self.srv_sock:
                    self.accept()
                else:
                    self.handle(s)

    def __init__(self, name):
        log.log("BrokerSrv.__init__()")
        self.name = name
        self.opened_sockets = []
        self.clients_list = []
        self.pat = re.compile('dbg disconnected')
        self.req_handlers = {
            'dispatcher': self.req_dispatcher,
            'cmd': self.req_cmd,
            'kill': self.req_kill
        }


if __name__ == "__main__":
    log = Logger(__file__, enable=False)

    PYTHON_BIN, PYTHON_PATH = get_python_path()
    if not os.path.exists(os.path.join(PYTHON_PATH, PYTHON_BIN)):
        log.log("broker failed to retreive PYTHON_PATH or PYTHON_BIN value.")
        sys.exit(0)

    parser = argparse.ArgumentParser()
    parser.add_argument('--idb', nargs=1, action='store')
    args = parser.parse_args()

    if not args.idb:
        log.log("[sync] no idb argument")
        sys.exit()

    config = get_config()
    PORT = config.getint("INTERFACE", 'port')
    HOST = config.get("INTERFACE", 'host')

    server = BrokerSrv(args.idb[0])

    try:
        server.bind()
    except Exception as e:
        server.announcement("failed to bind")
        log.log("Failed to bind: {}".format(repr(e)))
        sys.exit()

    try:
        server.notify()
    except Exception as e:
        server.announcement("failed to notify dispatcher")
        log.log("Failed to notify dispatcher: {}".format(repr(e)))
        sys.exit()

    try:
        server.loop()
    except Exception as e:
        server.announcement("broker stop")
        log.log("broker stop: {}".format(repr(e)))
