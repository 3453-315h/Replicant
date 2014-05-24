'''



Based on code from: https://gist.github.com/maxcountryman/676306
'''
import gevent
import logging
import functools

from gevent import socket, queue
from gevent.ssl import wrap_socket


class Tcp(object):
    '''Handles TCP connections, `timeout` is in secs.'''

    def __init__(self, host, port, timeout=300):
        self._ibuffer = ''
        self._obuffer = ''
        self.iqueue = queue.Queue()
        self.oqueue = queue.Queue()
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket = self._create_socket()

    def _create_socket(self):
        return socket.socket()

    def connect(self):
        self._socket.connect((self.host, self.port))
        try:
            jobs = [gevent.spawn(self._recv_loop), gevent.spawn(self._send_loop)]
            gevent.joinall(jobs)
        finally:
            gevent.killall(jobs)

    def disconnect(self):
        self._socket.close()

    def _recv_loop(self):
        while True:
            data = self._socket.recv(4096)
            self._ibuffer += data
            while '\r\n' in self._ibuffer:
                line, self._ibuffer = self._ibuffer.split('\r\n', 1)
                self.iqueue.put(line)

    def _send_loop(self):
        while True:
            line = self.oqueue.get().splitlines()[0][:500]
            self._obuffer += line.encode('utf-8', 'replace') + '\r\n'
            while self._obuffer:
                sent = self._socket.send(self._obuffer)
                self._obuffer = self._obuffer[sent:]


class SslTcp(Tcp):
    '''SSL wrapper for TCP connections.'''

    def _create_socket(self):
        return wrap_socket(Tcp._create_socket(self), server_side=False)


class IrcNullMessage(Exception):
    pass


class IrcBot(object):
    '''Provides a basic interface to an IRC server.'''

    msg_count = 0

    def __init__(self, settings):
        self.server = settings['server']
        self.nick = settings['nick']
        self.realname = settings['realname']
        self.port = settings['port']
        self.ssl = settings['ssl']
        self.channels = settings['channels']
        self.line = {'prefix': '', 'command': '', 'args': ['', '']}
        self.lines = queue.Queue() # responses from the server
        self.logger = settings['logger']
        self._connect()
        self._event_loop()

    def _log_info(self, record):
        if self.logger is not None:
            self.logger.info(record)

    def _log_debug(self, record):
        if self.logger is not None:
            self.logger.debug(record)

    def _log_warning(self, record):
        if self.logger is not None:
            self.logger.warning(record)

    def _create_connection(self):
        transport = SslTcp if self.ssl else Tcp
        return transport(self.server, self.port)

    def _connect(self):
        self.conn = self._create_connection()
        gevent.spawn(self.conn.connect)
        self._set_nick(self.nick)
        self.cmd('USER', (self.nick, ' 3 ', '* ', self.realname))

    def _disconnect(self):
        self.conn.disconnect()

    def _parsemsg(self, s):
        '''
        Breaks a message from an IRC server into its prefix, command,
        and arguments.
        '''
        prefix = ''
        trailing = []
        if not s:
            raise IrcNullMessage('Received an empty line from the server.')
        if s[0] == ':':
            prefix, s = s[1:].split(' ', 1)
        if s.find(' :') != -1:
            s, trailing = s.split(' :', 1)
            args = s.split()
            args.append(trailing)
        else:
            args = s.split()
        command = args.pop(0)
        return prefix, command, args

    def _event_loop(self):
        '''
        The main event loop.
        Data from the server is parsed here using `parsemsg`. Parsed events
        are put in the object's event queue, `self.events`.
        '''
        while True:
            self.msg_count += 1
            line = self.conn.iqueue.get()
            self._log_info(line)
            prefix, command, args = self._parsemsg(line)
            self.line = {'prefix': prefix, 'command': command, 'args': args}
            self.lines.put(self.line)
            if command == '433': # nick in use
                self.nick += '_'
                self._set_nick(self.nick)
            if command == 'PING':
                self.cmd('PONG', args)
            if command == '001':
                self._join_chans(self.channels)
            if command == 'PRIVMSG':
                if self.nick == args[0]:
                    args[0] = prefix.split('!')[0]
                self.privmsg(nick=prefix.split('!')[0], channel=args[0], msg=args[1])

    def _set_nick(self, nick):
        self.cmd('NICK', nick)

    def _join_chans(self, channels):
        return [self.cmd('JOIN', channel) for channel in channels]

    def privmsg(self, nick, channel, msg):
        self.say(channel, msg)

    def say(self, channel, msg):
        self.cmd('PRIVMSG', (channel + ' :' + msg))

    def cmd(self, command, args, prefix=None):
        if prefix:
            self._send(prefix + command + ' ' + ''.join(args))
        else:
            self._send(command + ' ' + ''.join(args))

    def _send(self, s):
        self._log_info(s)
        self.conn.oqueue.put(s)


if __name__ == '__main__':
    settings = {
        'server': 'irc.freenode.net',
        'nick': 'crackbot',
        'realname': 'CrackBot',
        'port': 6667,
        'ssl': False,
        'channels': ['#crackerbot',],
    }
    bot = lambda : IrcBot(settings)
    jobs = [gevent.spawn(bot)]
    gevent.joinall(jobs)