"""
Parses application/watcherinfo+xml documents according to RFC3857 and RFC3858.

Example:

>>> winfo_doc='''<?xml version="1.0"?>
... <watcherinfo xmlns="urn:ietf:params:xml:ns:watcherinfo"
...              version="0" state="full">
...   <watcher-list resource="sip:professor@example.net" package="presence">
...     <watcher status="active"
...              id="8ajksjda7s"
...              duration-subscribed="509"
...              event="approved" >sip:userA@example.net</watcher>
...     <watcher status="pending"
...              id="hh8juja87s997-ass7"
...              display-name="Mr. Subscriber"
...              event="subscribe">sip:userB@example.org</watcher>
...   </watcher-list>
... </watcherinfo>'''
>>> winfo = WatcherInfo()

The return value of winfo.update() is a dictionary containing WatcherList objects
as keys and lists of the updated watchers as values.

>>> updated = winfo.update(winfo_doc)
>>> len(updated['sip:professor@example.net'])
2

winfo.pending, winfo.terminated and winfo.active are dictionaries indexed by
WatcherList objects as keys and lists of Wacher objects as values.

>>> print winfo.pending['sip:professor@example.net'][0]
"Mr. Subscriber" <sip:userB@example.org>
>>> print winfo.pending['sip:professor@example.net'][1]
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
IndexError: list index out of range
>>> print winfo.active['sip:professor@example.net'][0]
sip:userA@example.net
>>> len(winfo.terminated['sip:professor@example.net'])
0

winfo.wlists is the list of WatcherList objects

>>> list(winfo.wlists[0].active) == list(winfo.active['sip:professor@example.net'])
True


See the classes for more information.
"""

from lxml import etree

from sipsimple.applications import XMLMeta, XMLApplication, XMLElement

__all__ = ['_namespace_',
           'NeedFullUpdateError',
           'WatcherInfoMeta',
           'Watcher', 
           'WatcherList',
           'WatcherInfo']


_namespace_ = 'urn:ietf:params:xml:ns:watcherinfo'

class NeedFullUpdateError(Exception): pass

class WatcherInfoMeta(XMLMeta): pass

class Watcher(XMLElement):
    """
    Definition for a watcher in a watcherinfo document
    
    Provides the attributes:
     * id
     * status
     * event
     * display_name
     * expiration
     * duration
     * sipuri

    Can be transformed to a string with the format DISPLAY_NAME <SIP_URI>.
    """
    _xml_tag = 'watcher'
    _xml_namespace = _namespace_
    _xml_attrs = {
            'id': {'id_attribute': True},
            'status': {},
            'event': {},
            'display_name': {'xml_attribute': 'display-name'},
            'expiration': {'test_equal': False},
            'duration': {'test_equal': False}}
    _xml_meta = WatcherInfoMeta

    def _parse_element(self, element):
        self.sipuri = element.text

    def __str__(self):
        return self.display_name and '"%s" <%s>' % (self.display_name, self.sipuri) or self.sipuri

WatcherInfoMeta.register(Watcher)

class WatcherList(XMLElement):
    """
    Definition for a list of watchers in a watcherinfo document
    
    It behaves like a list in that it can be indexed by a number, can be
    iterated and counted.

    It also provides the properties pending, active and terminated which are
    generators returning Watcher objects with the corresponding status.
    """
    _xml_tag = 'watcher-list'
    _xml_namespace = _namespace_
    _xml_attrs = {
            'resource': {'id_attribute': True},
            'package': {}}
    _xml_meta = WatcherInfoMeta

    def _parse_element(self, element, full_parse=True):
        self._watchers = {}
        if full_parse:
            self.update(element)

    def update(self, element):
        updated = []
        for child in element:
            watcher = Watcher.from_element(child)
            old = self._watchers.get(watcher.id)
            self._watchers[watcher.id] = watcher
            if old is None or old != watcher:
                updated.append(watcher)
        return updated
    
    def __iter__(self):
        return self._watchers.itervalues()

    def __getitem__(self, index):
        return self._watchers.values()[index]

    def __len__(self):
        return len(self._watchers)

    pending = property(lambda self: (watcher for watcher in self if watcher.status == 'pending'))
    waiting = property(lambda self: (watcher for watcher in self if watcher.status == 'waiting'))
    active = property(lambda self: (watcher for watcher in self if watcher.status == 'active'))
    terminated = property(lambda self: (watcher for watcher in self if watcher.status == 'terminated'))

WatcherInfoMeta.register(WatcherList)

class WatcherInfo(XMLApplication):
    """
    Definition for watcher info: a list of WatcherList elements
    
    The user agent instantiates this class once it subscribes to a *.winfo event
    and calls its update() method with the applicatin/watcherinfo+xml documents
    it receives via NOTIFY.

    The watchers can be accessed in two ways:
     1. via the wlists property, which returns a list of WatcherList elements;
     2. via the pending, active and terminated properties, which return
     dictionaries, mapping WatcherList objects to lists of Watcher objects.
     Since WatcherList objects can be compared for equality to SIP URI strings,
     representing the presentity to which the watchers have subscribed, the
     dictionaries can also be indexed by such strings.
    """
    
    accept_types = ['application/watcherinfo+xml']

    _xml_tag = 'watcherinfo'
    _xml_namespace = _namespace_
    _xml_meta = WatcherInfoMeta
    _xml_schema_file = 'watcherinfo.xsd'
    
    _parser_opts = {'remove_blank_text': True}
    
    def __init__(self):
        self.version = -1
        self._wlists = {}

    def _parse_element(self, element):
        self.version = -1
        self._wlists = {}
        self._update_from_element(element)
    
    def update(self, document):
        """
        Updates the state of this WatcherInfo object with data from an
        application/watcherinfo+xml document, passed as a string. 
        
        Will throw a NeedFullUpdateError if the current document is a partial
        update and the previous version wasn't received.
        """
        root = etree.XML(document, self._parser)
        return self._update_from_element(root)

    def _update_from_element(self, element):
        version = int(element.get('version'))
        state = element.get('state')

        if version <= self.version:
            return {}
        if state == 'partial' and version != self.version + 1:
            raise NeedFullUpdateError("Cannot update with version %d since last version received is %d" % (version, self.version))

        self.version = version

        updated_lists = {}
        if state == 'full':
            self._wlists = {}
            for xml_wlist in element:
                wlist = WatcherList.from_element(xml_wlist)
                self._wlists[wlist.resource] = wlist
                updated_lists[wlist] = list(wlist)
        elif state == 'partial':
            for xml_wlist in element:
                wlist = WatcherList.from_element(xml_wlist, full_parse=False)
                wlist = self._wlists.get(wlist.resource, wlist)
                self._wlists[wlist.resource] = wlist
                updated = wlist.update(xml_wlist)
                if updated:
                    updated_lists[wlist] = updated
        return updated_lists
    
    def __iter__(self):
        return self._wlists.itervalues()

    def __getitem__(self, index):
        return self._wlists[index]

    def __len__(self):
        return len(self._wlists)

    wlists = property(lambda self: self._wlists.values())
    pending = property(lambda self: dict((wlist, list(wlist.pending)) for wlist in self))
    waiting = property(lambda self: dict((wlist, list(wlist.waiting)) for wlist in self))
    active = property(lambda self: dict((wlist, list(wlist.active)) for wlist in self))
    terminated = property(lambda self: dict((wlist, list(wlist.terminated)) for wlist in self))

WatcherInfoMeta.register(WatcherInfo)