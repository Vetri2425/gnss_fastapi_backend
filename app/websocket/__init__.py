"""
WebSocket module for Socket.IO integration.

Provides async WebSocket handlers for real-time GNSS data streaming
and command control.
"""

from .handlers import WebSocketHandler

__all__ = ["WebSocketHandler"]
