#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys, os, datetime

try: # py2
    import ConfigParser
except ImportError: # py3
    import configparser as ConfigParser

HOST = "localhost"
PORT = 9100
CONF_NAME = '.sync'

def get_python_path():
    try:
        bin = os.environ['PYTHON_BIN']
        path = os.environ['PYTHON_PATH']
    except:
        bin, path = None, None

    if sys.platform == 'win32':
        bin = 'python.exe' if not bin else bin
        path = os.path.normpath("C:\\Python27") if not path else path
    elif sys.platform.startswith('linux') or sys.platform == 'darwin':
        bin = 'python' if not bin else bin
        path = os.path.normpath("/usr/bin") if not path else path
    else:
        print("[-] please fix PYTHON_PATH & PYTHON_BIN values, {} platform currently unknown".format(sys.platform))
        sys.exit(0)
    return (bin, path)

def get_config():
    config = None
    for loc in ['IDB_PATH', 'USERPROFILE', 'HOME']:
        if loc not in os.environ:
            continue
        confpath = os.path.join(os.path.realpath(os.environ[loc]), CONF_NAME)
        if not os.path.exists(confpath):
            continue
        config = ConfigParser.SafeConfigParser({'port': PORT, 'host': HOST})
        config.read(confpath)
        break
    return config

class Logger(object):
    def __init__(self, fname, enable=True):
        self.file = fname
        self.enable = enable
        self.clear()

    def write(self, mode, msg, indent=0):
        if not self.enable:
            return
        fd = open("{:s}.err".format(self.file), mode)
        msg = "  "*indent + msg + "\n"
        fd.write(msg)
        fd.close()

    def clear(self):
        date = datetime.datetime.now()
        msg = "New dispatcher started at: {:s}".format(str(date))
        self.write('w', msg)

    def log(self, msg, indent=0):
        self.write('a', msg)
