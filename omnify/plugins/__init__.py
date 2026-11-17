"""Built-in Omnify plugins.

Each subfolder is a self-contained plugin with a ``plugin.json`` manifest and
an entry point.  The :class:`~omnify.core.plugins.PluginManager` loads them
exactly like external drop-in plugins; being built in only means Omnify itself
is trusted to run their code.
"""
