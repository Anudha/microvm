"""Pluggable interfaces exposed to each instance.

An "interface" here is the mini-OS analogue of a syscall surface: the only
sanctioned way for an instance to touch the outside world. Each instance gets
its own interface objects, so state never leaks between instances.
"""
