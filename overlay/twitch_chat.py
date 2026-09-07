"""Read-only Twitch chat -> bounded Sonnet screening -> approved callback.

No Twitch token is required: the anonymous IRC connection cannot post chat.
Unapproved content is held in bounded memory, never written to the overlay log.
"""
import collections
import hashlib
import os
import queue
import re
import secrets
import socket
import ssl
import threading
import time
import unicodedata


# The names a viewer uses to address the agent directly. Being spoken to by
# name is the most valuable message class chat produces and the one that failed
# worst: on 2026-09-07 "downed wolf needs finished off and butchered. @Errata"
# was approved in milliseconds and then waited nearly six minutes behind other
# traffic, and the wolf lived four more turns. Nothing here or downstream ever
# filtered on "@" -- the live theory that it did is wrong -- but a mention now
# skips the per-viewer rate limit and is screened before ordinary chat.
#
# `chat_events.mentions_agent` in rimworld/instruments keeps a copy of this
# rule; the two trees deploy separately, so change the copies together.
AGENT_NAMES = tuple(n.strip() for n in (os.environ.get('RIMWORLD_AGENT_NAMES')
                                        or 'errata,claude_plays_rimworld').split(',')
                    if n.strip())
_MENTION = re.compile(r'(?<![0-9A-Za-z_])@?(?:%s)(?![0-9A-Za-z_])'
                      % '|'.join(re.escape(n) for n in AGENT_NAMES), re.IGNORECASE)


def mentions_agent(text):
    """Does this viewer message address the agent by name, with or without @?"""
    return bool(AGENT_NAMES) and bool(_MENTION.search(str(text or '')))


def parse_message(line, channel):
    """Parse only PRIVMSG for our exact channel; identity comes from IRC prefix."""
    tags = {}
    if line.startswith('@'):
        head, sep, line = line.partition(' ')
        if not sep:
            return None
        tags = dict(part.partition('=')[::2] for part in head[1:].split(';'))
    match = re.fullmatch(r':([A-Za-z0-9_]{1,25})![^ ]+ PRIVMSG #([A-Za-z0-9_]+) :(.*)', line)
    if not match or match[2].lower() != channel:
        return None
    username, text = match[1], match[3]
    if not text.strip() or len(text) > 500 or any(
            unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in text):
        return None
    # Use verified login rather than a viewer-controlled display-name tag.
    return {'name': username, 'text': text, 'message_id': tags.get('id', '')[:128]}


class TwitchChat:
    def __init__(self, channel, screener, publish, update, *, max_queue=20,
                 max_age=90, interval=2, user_interval=10, problem_users=None):
        channel = channel.lower().lstrip('#')
        if not re.fullmatch(r'[a-z0-9_]{1,25}', channel):
            raise ValueError('invalid Twitch channel')
        self.channel, self.screener = channel, screener
        self.publish, self.update = publish, update
        self.problem_users = problem_users
        self.queue = queue.Queue(maxsize=max_queue)
        # Bounded, lock-free (deque append/popleft are atomic) and drained
        # first by the worker. A flood of mentions still cannot grow memory:
        # maxlen drops the oldest, and the newest question is the live one.
        self.priority = collections.deque(maxlen=max(1, max_queue))
        self.max_age, self.interval, self.user_interval = max_age, interval, user_interval
        self.stop_event = threading.Event()
        self.connected = False
        self.guard = threading.Lock()
        self.seen = collections.OrderedDict()
        self.users = collections.OrderedDict()
        self.stats = dict(status='connecting', queued=0, priority=0, approved=0,
                          rejected=0, errors=0, dropped=0, mentions=0,
                          model=screener.model, channel=channel, lastDecision='')

    def report(self, **changes):
        with self.guard:
            self.stats.update(changes)
            self.stats['queued'] = self.queue.qsize()
            self.stats['priority'] = len(self.priority)
            self.update(dict(self.stats))

    def count(self, field, **changes):
        with self.guard:
            self.stats[field] += 1
            self.stats.update(changes)
            self.stats['queued'] = self.queue.qsize()
            self.stats['priority'] = len(self.priority)
            self.update(dict(self.stats))

    def submit(self, item):
        now = time.monotonic()
        key = item['message_id'] or hashlib.sha256(
            (item['name'] + '\0' + item['text']).encode()).hexdigest()
        if key in self.seen:
            return False
        self.seen[key] = now
        while len(self.seen) > 2048:
            self.seen.popitem(last=False)
        username = item['name'].lower()
        mention = mentions_agent(item.get('text'))
        # The per-viewer interval exists to stop one person filling the queue.
        # A viewer who has just called the agent by name is the case it must
        # not catch: that is a question waiting for an answer on stream.
        if not mention and now - self.users.get(username, -1e9) < self.user_interval:
            self.count('dropped', lastDecision='rate limited')
            return False
        self.users[username] = now
        self.users.move_to_end(username)
        while len(self.users) > 2048:
            self.users.popitem(last=False)
        if mention:
            self.priority.append((now, item))
            self.count('mentions', lastDecision='mention queued first')
            return True
        try:
            self.queue.put_nowait((now, item))
        except queue.Full:
            self.count('dropped', lastDecision='queue full')
            return False
        self.report()
        return True

    def take(self, timeout=.5):
        """(received, item, from_queue) for the next message to screen.

        Mentions first, always: an ordinary backlog must never make a viewer
        wait to be answered. Raises queue.Empty when there is nothing.
        """
        try:
            received, item = self.priority.popleft()
            return received, item, False
        except IndexError:
            received, item = self.queue.get(timeout=timeout)
            return received, item, True

    def process_one(self, received, item):
        if time.monotonic() - received > self.max_age:
            self.count('dropped', lastDecision='expired')
            return
        self.report(status='screening')
        failed = False
        try:
            result = self.screener.screen(item['name'], item['text'])
            if result.allowed is True:
                if time.monotonic() - received > self.max_age:
                    self.count('dropped', lastDecision='expired')
                    return
                # Publish original validated input, never model-generated text.
                self.publish(dict(item, approved=True, source='twitch',
                                  channel=self.channel, kind='chat'))
                self.count('approved', lastDecision='approved')
            else:
                error = result.category in ('error', 'unavailable', 'timeout', 'invalid_response')
                failed = error
                self.count('errors' if error else 'rejected',
                           lastDecision='screening unavailable' if error else 'held back')
                if not error and self.problem_users is not None:
                    try:
                        self.problem_users.record(item['name'], result.category)
                    except OSError:
                        self.report(lastDecision='held back; review log unavailable')
        except Exception:
            failed = True
            self.count('errors', lastDecision='screening unavailable')
        finally:
            self.report(status='error' if failed else ('ready' if self.connected else 'connecting'))

    def worker(self):
        while not self.stop_event.is_set():
            try:
                received, item, from_queue = self.take(timeout=.5)
            except queue.Empty:
                continue
            try:
                self.process_one(received, item)
            finally:
                if from_queue:
                    self.queue.task_done()
            self.stop_event.wait(self.interval)

    def reader(self):
        delay = 2
        while not self.stop_event.is_set():
            self.report(status='connecting')
            try:
                with socket.create_connection(('irc.chat.twitch.tv', 6697), timeout=10) as raw:
                    with ssl.create_default_context().wrap_socket(raw, server_hostname='irc.chat.twitch.tv') as sock:
                        sock.settimeout(15)
                        nick = 'justinfan' + str(secrets.randbelow(90000000) + 10000000)
                        sock.sendall(('PASS SCHMOOPIIE\r\nNICK ' + nick + '\r\n'
                                      'CAP REQ :twitch.tv/tags twitch.tv/commands\r\n'
                                      'JOIN #' + self.channel + '\r\n').encode())
                        pending = b''
                        last_data = time.monotonic()
                        joined_at = last_data
                        while not self.stop_event.is_set():
                            try:
                                data = sock.recv(16384)
                            except socket.timeout:
                                if time.monotonic() - last_data > 240 or (
                                        not self.connected and time.monotonic() - joined_at > 30):
                                    raise ConnectionError('Twitch idle or join timed out')
                                continue
                            if not data:
                                raise ConnectionError('Twitch disconnected')
                            last_data = time.monotonic()
                            pending += data
                            if len(pending) > 65536:
                                raise ValueError('oversized IRC buffer')
                            while b'\r\n' in pending:
                                line, pending = pending.split(b'\r\n', 1)
                                line = line.decode('utf-8', errors='replace')
                                if line.startswith('PING '):
                                    sock.sendall(('PONG ' + line[5:] + '\r\n').encode())
                                elif ' RECONNECT' in line:
                                    raise ConnectionError('Twitch reconnect')
                                elif f' 366 {nick} #{self.channel} ' in line:
                                    self.connected = True
                                    delay = 2
                                    self.report(status='ready')
                                else:
                                    item = parse_message(line, self.channel)
                                    if item and self.connected:
                                        self.submit(item)
            except Exception:
                self.connected = False
                self.report(status='error', lastDecision='Twitch reconnecting')
            self.stop_event.wait(delay)
            delay = min(60, delay * 2)

    def start(self):
        for name, target in [('twitch-reader', self.reader), ('chat-screening', self.worker)]:
            threading.Thread(target=target, name=name, daemon=True).start()
        return self

    def stop(self):
        self.stop_event.set()
