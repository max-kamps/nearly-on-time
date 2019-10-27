import re
from datetime import datetime as dt, timezone as tz
import logging

import discord.ext.commands as cmd
from discord.ext.commands.view import StringView

from .common import *
from .module import get_module_class
from . import persistence

log = logging.getLogger('bot')


class NearlyOnTime(cmd.Bot):
    def __init__(self, profile):
        super().__init__(command_prefix='', description='', pm_help=False, help_attrs={})

        super().remove_command('help')

        conf = persistence.get_module_shelf('core')

        if 'version' in conf and conf['version'] != 1:
            raise NotImplementedError(f'Main config is using an unsupported version: {main_shelf["version"]}')

        conf.setdefault('version', 1)
        conf.setdefault('superusers', [])
        conf.setdefault('active_modules', set())
        conf.sync()

        self.profile = profile
        self.conf = conf
        self.first_ready = None
        self.last_ready = None
        self.last_resume = None

        self.command_regex = None
        self.command_dms_regex = None

        self.modules = {}

        # The core module should always be loaded, so we can use eval to repair misconfigurations
        for module in {'core', *self.conf['active_modules']}:
            self.load_module(module)

    def load_module(self, name, persistent=True):
        C = get_module_class(name)

        if name in self.modules:
            self.remove_cog(name)

        instance = C(self)
        self.modules[name] = instance
        self.add_cog(instance)

        if persistent:
            self.conf['active_modules'].add(name)
            self.conf.sync()

    def unload_module(self, name, persistent=True):
        self.remove_cog(name)
        if name in self.modules:
            del self.modules[name]
        
        if persistent:
            self.conf['active_modules'].discard(name)
            self.conf.sync()

    async def on_ready(self):
        log.info(f'Ready with Username {self.user.name!r}, ID {self.user.id!r}')

        now = dt.now(tz.utc)
        if self.first_ready is None:
            self.first_ready = now

        self.last_ready = now

        self.command_regex = re.compile(fr'(?s)^<@!?{self.user.id}>(.*)$')
        self.command_dms_regex = re.compile(fr'(?s)^(?:<@!?{self.user.id}>)?(.*)$')

    async def on_resumed(self):
        log.warning(f'Resumed')
        self.last_resume = dt.now(tz.utc)

    async def get_context(self, message, *, cls=cmd.Context):
        # This function is called internally by discord.py.
        # We have to fiddle with it because we are using a dynamic prefix (our mention string),
        # as well as no prefix inside of DMs.
        # The included prefix matching functions could not deal with this case.
        # If it ever becomes possible, we should probably switch to that.

        # Frankly, I don't really remember what I did here, but it might be good
        # to periodically check the get_context method on the base class and
        # port over any changes that happened there. ~hmry (2019-08-14, 02:25)

        if self.command_regex is None:
            return cls(prefix=None, view=None, bot=self, message=message)

        cmd_regex = self.command_dms_regex if message.guild is None else self.command_regex
        match = cmd_regex.match(message.content)

        if not match:
            return cls(prefix=None, view=None, bot=self, message=message)

        view = StringView(match.group(1).strip())
        ctx = cls(prefix=None, view=view, bot=self, message=message)

        if self._skip_check(message.author.id, self.user.id):
            return ctx

        invoker = view.get_word()
        ctx.invoked_with = invoker
        ctx.command = self.all_commands.get(invoker)
        return ctx

    async def close(self):
        log.info('Closing...')
        for mod in self.modules.copy():
            self.unload_module(mod, persistent=False)
        
        await super().close()
