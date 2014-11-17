import getopt
import gettext
import locale
import os
import sys

import mailpile.util
import mailpile.defaults
from mailpile.commands import COMMANDS, Command, Action
from mailpile.commands import Help, HelpSplash, Load, Rescan
from mailpile.config import ConfigManager
from mailpile.conn_brokers import DisableUnbrokeredConnections
from mailpile.i18n import gettext as _
from mailpile.i18n import ngettext as _n
from mailpile.plugins import PluginManager
from mailpile.ui import ANSIColors, Session, UserInteraction, Completer
from mailpile.util import *

_plugins = PluginManager(builtin=__file__)

# This makes sure mailbox "plugins" get loaded... has to go somewhere?
from mailpile.mailboxes import *

# This is also a bit silly, should be somewhere else?
Help.ABOUT = mailpile.defaults.ABOUT

# We may try to load readline later on... maybe?
readline = None


##[ Main ]####################################################################


def Interact(session):
    global readline
    try:
        import readline as rl  # Unix-only
        readline = rl
    except ImportError:
        pass

    try:
        if readline:
            readline.read_history_file(session.config.history_file())
            readline.set_completer_delims(Completer.DELIMS)
            readline.set_completer(Completer(session).get_completer())
            for opt in ["tab: complete", "set show-all-if-ambiguous on"]:
                readline.parse_and_bind(opt)
    except IOError:
        pass

    # Negative history means no saving state to disk.
    history_length = session.config.sys.history_length
    if readline is None:
        pass  # history currently not supported under Windows / Mac
    elif history_length >= 0:
        readline.set_history_length(history_length)
    else:
        readline.set_history_length(-history_length)

    try:
        prompt = session.ui.term.color('mailpile> ',
                                       color=session.ui.term.BLACK,
                                       weight=session.ui.term.BOLD)
        while not mailpile.util.QUITTING:
            session.ui.block()
            opt = raw_input(prompt).decode('utf-8').strip()
            session.ui.term.check_max_width()
            session.ui.unblock()
            if opt:
                if ' ' in opt:
                    opt, arg = opt.split(' ', 1)
                else:
                    arg = ''
                try:
                    session.ui.display_result(Action(session, opt, arg))
                except UsageError, e:
                    session.error(unicode(e))
                except UrlRedirectException, e:
                    session.error('Tried to redirect to: %s' % e.url)
    except EOFError:
        print
    finally:
        session.ui.unblock()

    try:
        if session.config.sys.history_length > 0:
            readline.write_history_file(session.config.history_file())
        else:
            safe_remove(session.config.history_file())
    except OSError:
        pass


class InteractCommand(Command):
    SYNOPSIS = (None, 'interact', None, None)
    ORDER = ('Internals', 2)
    CONFIG_REQUIRED = False
    RAISES = (KeyboardInterrupt,)

    def command(self):
        session, config = self.session, self.session.config

        session.interactive = True
        if sys.stdout.isatty() and sys.platform[:3] != "win":
            session.ui.term = ANSIColors()

        # Create and start the rest of the threads, load the index.
        if config.loaded_config:
            Load(session, '').run(quiet=True)
        else:
            config.prepare_workers(session, daemons=True)

        session.ui.display_result(HelpSplash(session, 'help', []).run())
        Interact(session)

        return self._success(_('Ran interactive shell'))


class WaitCommand(Command):
    SYNOPSIS = (None, 'wait', None, None)
    ORDER = ('Internals', 2)
    CONFIG_REQUIRED = False
    RAISES = (KeyboardInterrupt,)

    def command(self):
        self.session.ui.display_result(HelpSplash(self.session, 'help', []
                                                  ).run(interactive=False))
        while not mailpile.util.QUITTING:
            time.sleep(1)
        return self._success(_('Did nothing much for a while'))


def Main(args):
    DisableUnbrokeredConnections()

    # Bootstrap translations until we've loaded everything else
    mailpile.i18n.ActivateTranslation(None, ConfigManager, None)
    try:
        # Create our global config manager and the default (CLI) session
        config = ConfigManager(rules=mailpile.defaults.CONFIG_RULES)
        session = Session(config)
        cli_ui = session.ui = UserInteraction(config)
        session.main = True
        try:
            config.clean_tempfile_dir()
            config.load(session)
        except IOError:
            session.ui.error(_('Failed to decrypt configuration, '
                               'please log in!'))
        config.prepare_workers(session)
    except AccessError, e:
        session.ui.error('Access denied: %s\n' % e)
        sys.exit(1)

    try:
        try:
            shorta, longa = '', []
            for cls in COMMANDS:
                shortn, longn, urlpath, arglist = cls.SYNOPSIS[:4]
                if arglist:
                    if shortn:
                        shortn += ':'
                    if longn:
                        longn += '='
                if shortn:
                    shorta += shortn
                if longn:
                    longa.append(longn.replace(' ', '_'))

            opts, args = getopt.getopt(args, shorta, longa)
            for opt, arg in opts:
                session.ui.display_result(Action(
                    session, opt.replace('-', ''), arg.decode('utf-8')))
            if args:
                session.ui.display_result(Action(
                    session, args[0], ' '.join(args[1:]).decode('utf-8')))

        except (getopt.GetoptError, UsageError), e:
            session.error(unicode(e))

        if not opts and not args:
            InteractCommand(session).run()

    except KeyboardInterrupt:
        pass

    finally:
        if readline:
            readline.write_history_file(session.config.history_file())

        # Make everything in the background quit ASAP...
        mailpile.util.LAST_USER_ACTIVITY = 0
        mailpile.util.QUITTING = True

        if config.plugins:
            config.plugins.process_shutdown_hooks()

        config.stop_workers()
        if config.index:
            config.index.save_changes()
        if config.event_log:
            config.event_log.close()

        if session.interactive and config.sys.debug:
            session.ui.display_result(Action(session, 'ps', ''))

        # Remove anything that we couldn't remove before
        safe_remove()


_plugins.register_commands(InteractCommand, WaitCommand)

if __name__ == "__main__":
    Main(sys.argv[1:])
