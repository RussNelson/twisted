# -*- test-case-name: twisted.mail.test.test_mail -*-
# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Support for relaying mail.
"""

from twisted.mail import smtp
from twisted.python import log
from twisted.internet.address import UNIXAddress

import os

try:
    import cPickle as pickle
except ImportError:
    import pickle



class AbstractRelayRules(object):
    """
    A base class for relay rules which determine whether a message should be
    relayed.
    """
    def willRelay(self, address, protocol, authorized):
        """
        Determine whether a message should be relayed.

        @type address: L{Address}
        @param address: The destination address.

        @type protocol: L{Protocol <twisted.internet.protocol.Protocol>}
        @param protocol: The protocol over which the message was received.

        @type authorized: L{bool}
        @param authorized: A flag indicating whether the originator has been
            authorized.

        @rtype: L{bool}
        @return: An indication of whether the message should be relayed.
        """
        return False



class DomainQueuerRelayRules(object):
    """
    The default relay rules for a L{DomainQueuer}.
    """
    def willRelay(self, address, protocol, authorized):
        """
        Determine whether a message should be relayed.

        Relay for all messages received over UNIX sockets or from localhost or
        when the message originator has been authenticated.

        @type address: L{Address}
        @param address: The destination address.

        @type protocol: L{Protocol <twisted.internet.protocol.Protocol>}
        @param protocol: The protocol over which the message was received.

        @type authorized: L{bool}
        @param authorized: A flag indicating whether the originator has been
            authorized.

        @rtype: L{bool}
        @return: An indication of whether the message should be relayed.
        """
        peer = protocol.transport.getPeer()
        return (authorized or isinstance(peer, UNIXAddress) or
            peer.host == '127.0.0.1')



class DomainQueuer:
    """
    A domain which adds messages to a queue for relaying.

    @ivar service: See L{__init__}

    @type authed: L{bool}
    @ivar authed: A flag indicating whether the originator of the message has
        been authenticated.

    @type relayRules: L{AbstractRelayRules}
    @ivar relayRules: The rules to determine whether a message should be
        relayed.
    """
    def __init__(self, service, authenticated=False, relayRules=None):
        """
        @type service: L{MailService}
        @param service: An email service.

        @type authenticated: L{bool}
        @param authenticated: A flag indicating whether the originator of the
            message has been authenticated.

        @type relayRules: L{NoneType <types.NoneType>} or L{AbstractRelayRules}
        @param relayRules: The rules to determine whether a message
            should be relayed.
        """
        self.service = service
        self.authed = authenticated
        self.relayRules = relayRules
        if not self.relayRules:
            self.relayRules = DomainQueuerRelayRules()


    def exists(self, user):
        """
        Check whether mail can be relayed to a user.

        @type user: L{User}
        @param user: A user.

        @rtype: no-argument callable which returns L{IMessage <smtp.IMessage>}
            provider
        @return: A function which takes no arguments and returns a message
            receiver for the user.

        @raise SMTPBadRcpt: When mail cannot be relayed to the user.
        """
        if self.willRelay(user.dest, user.protocol):
            # The most cursor form of verification of the addresses
            orig = filter(None, str(user.orig).split('@', 1))
            dest = filter(None, str(user.dest).split('@', 1))
            if len(orig) == 2 and len(dest) == 2:
                return lambda: self.startMessage(user)
        raise smtp.SMTPBadRcpt(user)


    def willRelay(self, address, protocol):
        """
        Determine whether a message should be relayed according to the relay
        rules.

        @type address: L{Address}
        @param address: The destination address.

        @type protocol: L{Protocol <twisted.internet.protocol.Protocol>}
        @param protocol: The protocol over which the message was received.

        @rtype: L{bool}
        @return: An indication of whether the message should be relayed.
        """
        return self.relayRules.willRelay(address, protocol, self.authed)


    def startMessage(self, user):
        """
        Create an envelope and a message receiver for the relay queue.

        @type user: L{User}
        @param user: A user.

        @rtype: L{IMessage <smtp.IMessage>}
        @return: A message receiver.
        """
        queue = self.service.queue
        envelopeFile, smtpMessage = queue.createNewMessage()
        try:
            log.msg('Queueing mail %r -> %r' % (str(user.orig),
                str(user.dest)))
            pickle.dump([str(user.orig), str(user.dest)], envelopeFile)
        finally:
            envelopeFile.close()
        return smtpMessage



class RelayerMixin:

    # XXX - This is -totally- bogus
    # It opens about a -hundred- -billion- files
    # and -leaves- them open!

    def loadMessages(self, messagePaths):
        self.messages = []
        self.names = []
        for message in messagePaths:
            fp = open(message + '-H')
            try:
                messageContents = pickle.load(fp)
            finally:
                fp.close()
            fp = open(message + '-D')
            messageContents.append(fp)
            self.messages.append(messageContents)
            self.names.append(message)


    def getMailFrom(self):
        if not self.messages:
            return None
        return self.messages[0][0]


    def getMailTo(self):
        if not self.messages:
            return None
        return [self.messages[0][1]]


    def getMailData(self):
        if not self.messages:
            return None
        return self.messages[0][2]


    def sentMail(self, code, resp, numOk, addresses, log):
        """Since we only use one recipient per envelope, this
        will be called with 0 or 1 addresses. We probably want
        to do something with the error message if we failed.
        """
        if code in smtp.SUCCESS:
            # At least one, i.e. all, recipients successfully delivered
            os.remove(self.names[0] + '-D')
            os.remove(self.names[0] + '-H')
        del self.messages[0]
        del self.names[0]



class SMTPRelayer(RelayerMixin, smtp.SMTPClient):
    """
    A base class for SMTP relayers.
    """
    def __init__(self, messagePaths, *args, **kw):
        """
        @type messagePaths: L{list} of L{bytes}
        @param messagePaths: The base filename for each message to be relayed.

        @type args: 1-L{tuple} of (0) L{bytes} or 2-L{tuple} of
            (0) L{bytes}, (1) L{int}
        @param args: Positional arguments for L{SMTPClient.__init__}

        @type kw: L{dict}
        @param kw: Keyword arguments for L{SMTPClient.__init__}
        """
        smtp.SMTPClient.__init__(self, *args, **kw)
        self.loadMessages(messagePaths)



class ESMTPRelayer(RelayerMixin, smtp.ESMTPClient):
    """
    A base class for ESMTP relayers.
    """
    def __init__(self, messagePaths, *args, **kw):
        """
        @type messagePaths: L{list} of L{bytes}
        @param messagePaths: The base filename for each message to be relayed.

        @type args: 3-L{tuple} of (0) L{bytes}, (1) L{NoneType
            <types.NoneType>} or L{ClientContextFactory
            <twisted.internet.ssl.ClientContextFactory>}, (2) L{bytes} or
            4-L{tuple} of (0) L{bytes}, (1) L{NoneType <types.NoneType>}
            or L{ClientContextFactory
            <twisted.internet.ssl.ClientContextFactory>}, (2) L{bytes},
            (3) L{int}
        @param args: Positional arguments for L{ESMTPClient.__init__}

        @type kw: L{dict}
        @param kw: Keyword arguments for L{ESMTPClient.__init__}
        """
        smtp.ESMTPClient.__init__(self, *args, **kw)
        self.loadMessages(messagePaths)
