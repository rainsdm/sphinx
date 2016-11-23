# -*- coding: utf-8 -*-
"""
    sphinx.builders.applehelp
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    Build Apple help books.

    :copyright: Copyright 2007-2016 by the Sphinx team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""
from __future__ import print_function

import codecs
import pipes

from os import path, environ
import shlex

from sphinx.builders.html import StandaloneHTMLBuilder
from sphinx.config import string_classes
from sphinx.util.osutil import copyfile, ensuredir, make_filename
from sphinx.util.console import bold  # type: ignore
from sphinx.util.fileutil import copy_asset
from sphinx.util.pycompat import htmlescape
from sphinx.util.matching import Matcher
from sphinx.errors import SphinxError

import plistlib
import subprocess

if False:
    # For type annotation
    from sphinx.application import Sphinx  # NOQA

# Use plistlib.dump in 3.4 and above
try:
    write_plist = plistlib.dump  # type: ignore
except AttributeError:
    write_plist = plistlib.writePlist


# False access page (used because helpd expects strict XHTML)
access_page_template = '''\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"\
 "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>%(title)s</title>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <meta name="robots" content="noindex" />
    <meta http-equiv="refresh" content="0;url=%(toc)s" />
  </head>
  <body>
  </body>
</html>
'''


class AppleHelpIndexerFailed(SphinxError):
    category = 'Help indexer failed'


class AppleHelpCodeSigningFailed(SphinxError):
    category = 'Code signing failed'


class AppleHelpBuilder(StandaloneHTMLBuilder):
    """
    Builder that outputs an Apple help book.  Requires Mac OS X as it relies
    on the ``hiutil`` command line tool.
    """
    name = 'applehelp'

    # don't copy the reST source
    copysource = False
    supported_image_types = ['image/png', 'image/gif', 'image/jpeg',
                             'image/tiff', 'image/jp2', 'image/svg+xml']

    # don't add links
    add_permalinks = False

    # this is an embedded HTML format
    embedded = True

    # don't generate the search index or include the search page
    search = False

    def init(self):
        # type: () -> None
        super(AppleHelpBuilder, self).init()
        # the output files for HTML help must be .html only
        self.out_suffix = '.html'
        self.link_suffix = '.html'

        if self.config.applehelp_bundle_id is None:
            raise SphinxError('You must set applehelp_bundle_id before '
                              'building Apple Help output')

        self.bundle_path = path.join(self.outdir,
                                     self.config.applehelp_bundle_name +
                                     '.help')
        self.outdir = path.join(self.bundle_path,
                                'Contents',
                                'Resources',
                                self.config.applehelp_locale + '.lproj')

    def handle_finish(self):
        # type: () -> None
        super(AppleHelpBuilder, self).handle_finish()

        self.finish_tasks.add_task(self.copy_localized_files)
        self.finish_tasks.add_task(self.build_helpbook)

    def copy_localized_files(self):
        # type: () -> None
        source_dir = path.join(self.confdir, self.config.applehelp_locale + '.lproj')
        target_dir = self.outdir

        if path.isdir(source_dir):
            self.info(bold('copying localized files... '), nonl=True)

            excluded = Matcher(self.config.exclude_patterns + ['**/.*'])
            copy_asset(source_dir, target_dir, excluded,
                       context=self.globalcontext, renderer=self.templates)

            self.info('done')

    def build_helpbook(self):
        # type: () -> None
        contents_dir = path.join(self.bundle_path, 'Contents')
        resources_dir = path.join(contents_dir, 'Resources')
        language_dir = path.join(resources_dir,
                                 self.config.applehelp_locale + '.lproj')

        for d in [contents_dir, resources_dir, language_dir]:
            ensuredir(d)

        # Construct the Info.plist file
        toc = self.config.master_doc + self.out_suffix

        info_plist = {
            'CFBundleDevelopmentRegion': self.config.applehelp_dev_region,
            'CFBundleIdentifier': self.config.applehelp_bundle_id,
            'CFBundleInfoDictionaryVersion': '6.0',
            'CFBundlePackageType': 'BNDL',
            'CFBundleShortVersionString': self.config.release,
            'CFBundleSignature': 'hbwr',
            'CFBundleVersion': self.config.applehelp_bundle_version,
            'HPDBookAccessPath': '_access.html',
            'HPDBookIndexPath': 'search.helpindex',
            'HPDBookTitle': self.config.applehelp_title,
            'HPDBookType': '3',
            'HPDBookUsesExternalViewer': False,
        }

        if self.config.applehelp_icon is not None:
            info_plist['HPDBookIconPath'] \
                = path.basename(self.config.applehelp_icon)

        if self.config.applehelp_kb_url is not None:
            info_plist['HPDBookKBProduct'] = self.config.applehelp_kb_product
            info_plist['HPDBookKBURL'] = self.config.applehelp_kb_url

        if self.config.applehelp_remote_url is not None:
            info_plist['HPDBookRemoteURL'] = self.config.applehelp_remote_url

        self.info(bold('writing Info.plist... '), nonl=True)
        with open(path.join(contents_dir, 'Info.plist'), 'wb') as f:
            write_plist(info_plist, f)
        self.info('done')

        # Copy the icon, if one is supplied
        if self.config.applehelp_icon:
            self.info(bold('copying icon... '), nonl=True)

            try:
                copyfile(path.join(self.srcdir, self.config.applehelp_icon),
                         path.join(resources_dir, info_plist['HPDBookIconPath']))

                self.info('done')
            except Exception as err:
                self.warn('cannot copy icon file %r: %s' %
                          (path.join(self.srcdir, self.config.applehelp_icon),
                           err))
                del info_plist['HPDBookIconPath']

        # Build the access page
        self.info(bold('building access page...'), nonl=True)
        with codecs.open(path.join(language_dir, '_access.html'), 'w') as f:
            f.write(access_page_template % {
                'toc': htmlescape(toc, quote=True),
                'title': htmlescape(self.config.applehelp_title)
            })
        self.info('done')

        # Generate the help index
        self.info(bold('generating help index... '), nonl=True)

        args = [
            self.config.applehelp_indexer_path,
            '-Cf',
            path.join(language_dir, 'search.helpindex'),
            language_dir
        ]

        if self.config.applehelp_index_anchors is not None:
            args.append('-a')

        if self.config.applehelp_min_term_length is not None:
            args += ['-m', '%s' % self.config.applehelp_min_term_length]

        if self.config.applehelp_stopwords is not None:
            args += ['-s', self.config.applehelp_stopwords]

        if self.config.applehelp_locale is not None:
            args += ['-l', self.config.applehelp_locale]

        if self.config.applehelp_disable_external_tools:
            self.info('skipping')

            self.warn('you will need to index this help book with:\n  %s'
                      % (' '.join([pipes.quote(arg) for arg in args])))
        else:
            try:
                p = subprocess.Popen(args,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)

                output = p.communicate()[0]

                if p.returncode != 0:
                    raise AppleHelpIndexerFailed(output)
                else:
                    self.info('done')
            except OSError:
                raise AppleHelpIndexerFailed('Command not found: %s' % args[0])

        # If we've been asked to, sign the bundle
        if self.config.applehelp_codesign_identity:
            self.info(bold('signing help book... '), nonl=True)

            args = [
                self.config.applehelp_codesign_path,
                '-s', self.config.applehelp_codesign_identity,
                '-f'
            ]

            args += self.config.applehelp_codesign_flags

            args.append(self.bundle_path)

            if self.config.applehelp_disable_external_tools:
                self.info('skipping')

                self.warn('you will need to sign this help book with:\n  %s'
                          % (' '.join([pipes.quote(arg) for arg in args])))
            else:
                try:
                    p = subprocess.Popen(args,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)

                    output = p.communicate()[0]

                    if p.returncode != 0:
                        raise AppleHelpCodeSigningFailed(output)
                    else:
                        self.info('done')
                except OSError:
                    raise AppleHelpCodeSigningFailed('Command not found: %s' % args[0])


def setup(app):
    # type: (Sphinx) -> None
    app.setup_extension('sphinx.builders.html')
    app.add_builder(AppleHelpBuilder)

    app.add_config_value('applehelp_bundle_name',
                         lambda self: make_filename(self.project), 'applehelp')
    app.add_config_value('applehelp_bundle_id', None, 'applehelp', string_classes)
    app.add_config_value('applehelp_dev_region', 'en-us', 'applehelp')
    app.add_config_value('applehelp_bundle_version', '1', 'applehelp')
    app.add_config_value('applehelp_icon', None, 'applehelp', string_classes)
    app.add_config_value('applehelp_kb_product',
                         lambda self: '%s-%s' % (make_filename(self.project), self.release),
                         'applehelp')
    app.add_config_value('applehelp_kb_url', None, 'applehelp', string_classes)
    app.add_config_value('applehelp_remote_url', None, 'applehelp', string_classes)
    app.add_config_value('applehelp_index_anchors', False, 'applehelp', string_classes)
    app.add_config_value('applehelp_min_term_length', None, 'applehelp', string_classes)
    app.add_config_value('applehelp_stopwords',
                         lambda self: self.language or 'en', 'applehelp')
    app.add_config_value('applehelp_locale', lambda self: self.language or 'en', 'applehelp')
    app.add_config_value('applehelp_title', lambda self: self.project + ' Help', 'applehelp')
    app.add_config_value('applehelp_codesign_identity',
                         lambda self: environ.get('CODE_SIGN_IDENTITY', None),
                         'applehelp')
    app.add_config_value('applehelp_codesign_flags',
                         lambda self: shlex.split(environ.get('OTHER_CODE_SIGN_FLAGS', '')),
                         'applehelp')
    app.add_config_value('applehelp_indexer_path', '/usr/bin/hiutil', 'applehelp')
    app.add_config_value('applehelp_codesign_path', '/usr/bin/codesign', 'applehelp')
    app.add_config_value('applehelp_disable_external_tools', False, None)
