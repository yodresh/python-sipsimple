import sys
from pprint import pformat
from zope.interface import implements
from application.notification import IObserver


class FileLoggerBase(object):

    def __init__(self, fileobj=None):
        if fileobj is None:
            fileobj = sys.stderr
        self.fileobj = fileobj

    def write(self, msg):
        self.fileobj.write(msg + '\n')

    def register_observer(self, notification_center):
        notification_center.add_observer(self, self.event_name)

    def unregister_observer(self, notification_center):
        notification_center.remove_observer(self, self.event_name)


class SIPTracer(FileLoggerBase):

    implements(IObserver)

    event_name = 'SCEngineSIPTrace'
    enabled = True

    def __init__(self, fileobj=None):
        FileLoggerBase.__init__(self, fileobj)
        self.start_time = None
        self.packet_count = 0

    def handle_notification(self, notification):
        if not self.enabled:
            return
        timestamp = notification.data.timestamp
        if self.start_time is None:
            self.start_time = timestamp
        self.packet_count += 1
        if notification.data.received:
            direction = "RECEIVED"
        else:
            direction = "SENDING"
        buf = ["%s: Packet %d, +%s" % (direction, self.packet_count, (timestamp - self.start_time))]
        buf.append("%(timestamp)s: %(source_ip)s:%(source_port)d --> %(destination_ip)s:%(destination_port)d" %
                   notification.data.__dict__)
        buf.append(notification.data.data)
        self.write('\n'.join(buf))


class PJSIPTracer(FileLoggerBase):

    implements(IObserver)

    event_name = "SCEngineLog"
    enabled = True

    def handle_notification(self, notification):
        if not self.enabled:
            return
        self.write("%(timestamp)s (%(level)d) %(sender)14s: %(message)s" % notification.data.__dict__)


class EngineTracer(FileLoggerBase):

    implements(IObserver)

    excluded_notifications  = ["SCEngineLog"]

    def register_observer(self, notification_center):
        notification_center.add_observer(self)

    def unregister_observer(self, notification_center):
        notification_center.remove_observer(self)

    def handle_notification(self, notification):
        if notification.name in self.excluded_notifications:
            return
        self.write("Notification name=%r sender=%r\n%s" % (notification.name, notification.sender,
                                                           pformat(notification.data.__dict__)))


class StateLoggerBase(FileLoggerBase):

    implements(IObserver)

    def handle_notification(self, notification):
        if notification.name == self.event_name:
            state = notification.data.state
            try:
                func = getattr(self, 'log_state_%s' % state.lower())
            except AttributeError:
                return self.log_state_default(notification.data)
            else:
                return func(notification.data)
        else:
            try:
                func = getattr(self, 'log_%s' % notification.name)
            except AttributeError:
                pass
            else:
                return func(notification.data)

    def log_state_default(self, notification_data):
        pass

    def register_observer(self, notification_center, sender, observer=None):
        self.obj = sender
        if observer is None:
            observer = self
        notification_center.add_observer(observer, self.event_name, self.obj)

    def unregister_observer(self, notification_center, sender, observer=None):
        self.obj = sender
        if observer is None:
            observer = self
        notification_center.remove_observer(observer, self.event_name, self.obj)


def _format_reason(notification_data):
    code = getattr(notification_data, 'code', None)
    reason = getattr(notification_data, 'reason', None)
    if (code, reason)==(200, 'OK'): # boring
        return ''
    result = ''
    if code not in [None, 200]:
        result += str(code)
    if reason:
        if result:
            result += ' '
        result += str(reason)
    if result:
        result = ' (%s)' % result
    return result


class RegistrationLogger(StateLoggerBase):
    """Log registration attempts.

    After a successful registration, only log registration failures.
    """
    registered_count = 0
    event_name = 'SCRegistrationChangedState'

    def log_state_default(self, notification_data):
        state = notification_data.state
        x = (state.capitalize(), self.obj.credentials.uri, self.obj.route.address, self.obj.route.port,
             _format_reason(notification_data))
        self.write('%s %s at %s:%s%s' % x)

    def log_state_registering(self, notification_data):
        if self.registered_count<=0:
            return self.log_state_default(notification_data)

    def log_state_unregistering(self, notification_data):
        pass

    def log_state_unregistered(self, notification_data):
        if notification_data.code!=200:
            self.registered_count = 0
            return self.log_state_default(notification_data)

    def log_state_registered(self, notification_data):
        if self.registered_count <= 0 or notification_data.code!=200:
            self.registered_count = 0
            x = (notification_data.contact_uri, notification_data.expires, _format_reason(notification_data))
            self.write("Registered SIP contact address: %s (expires in %d seconds)%s" % x)
        self.registered_count += 1


class InvitationLogger(StateLoggerBase):

    event_name = 'SCInvitationChangedState'
    confirmed = False

    def register_observer(self, notification_center, sender, observer=None):
        self.obj = sender
        if observer is None:
            observer = self
        notification_center.add_observer(observer, 'SCInvitationGotSDPUpdate', self.obj)
        notification_center.add_observer(observer, self.event_name, self.obj)

    def unregister_observer(self, notification_center, sender, observer=None):
        self.obj = sender
        if observer is None:
            observer = self
        notification_center.remove_observer(observer, self.event_name, self.obj)
        notification_center.remove_observer(observer, 'SCInvitationGotSDPUpdate', self.obj)

    @property
    def session_name(self):
        return 'SIP session'

    def _format_to(self):
        return 'to %s' % self.obj.remote_uri

    def _format_fromtoproxy(self):
        result = 'from %s to %s' % (self.obj.local_uri, self.obj.remote_uri)
        if self.obj.route:
            result += " through proxy %s:%d" % (self.obj.route.address, self.obj.route.port)
        return result

    def _get_verb(self, state, prev_state):
        # only if connection was not established yet and if we initiated the disconnect
        if not self.confirmed and 'DISCONNECTING' in [state, prev_state]:
            if self.obj.is_outgoing:
                return {'DISCONNECTED': 'Cancelled',
                        'DISCONNECTING': 'Cancelling'}.get(state, state).capitalize()
            else:
                return {'DISCONNECTED': 'Rejected',
                        'DISCONNECTING': 'Rejecting'}.get(state, state).capitalize()
        return state.capitalize()

    def _format_state_default(self, notification_data):
        reason = _format_reason(notification_data)
        state = notification_data.state
        prev_state = notification_data.prev_state
        return '%s %s %s%s' % (self._get_verb(state, prev_state), self.session_name, self._format_to(), reason)

    def log_state_default(self, notification_data):
        self.write(self._format_state_default(notification_data))

    def log_state_calling(self, notification_data):
        try:
            self.__last_calling_message
        except AttributeError:
            self.__last_calling_message = None
        msg = 'Initiating %s %s...' % (self.session_name, self._format_fromtoproxy())
        if msg != self.__last_calling_message: # filter out successive Calling messages
            self.write(msg)
            self.__last_calling_message = msg

    def log_state_incoming(self, notification_data):
        pass

    def log_state_confirmed(self, notification_data):
        self.confirmed = True

    def log_state_early(self, notification_data):
        try:
            headers = notification_data.headers
        except AttributeError:
            pass # we're the party that issued Ringing
        else:
            agent = headers.get('User-Agent', '')
            contact = str(headers.get('Contact', [['']])[0][0])
            if agent:
                contact += ' (%s)' % agent
            self.write('Ringing from %s' % contact)

    def log_SCInvitationGotSDPUpdate(self, notification_data):
        if not notification_data.succeeded:
            self.write('SDP negotiation failed: %s' % notification_data.error)

