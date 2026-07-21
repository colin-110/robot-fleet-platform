import { useState, useEffect, useRef, useCallback } from 'react';

const WS_API_KEY = import.meta.env.VITE_WS_API_KEY || 'fleet-secret-key-2026';

export function useWebSocket(url) {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState(null);
  const ws = useRef(null);
  const reconnectTimeout = useRef(null);
  const pingInterval = useRef(null);
  const shouldReconnect = useRef(true);

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return;

    // Append API key as query parameter for WebSocket auth
    const separator = url.includes('?') ? '&' : '?';
    const authenticatedUrl = `${url}${separator}api_key=${encodeURIComponent(WS_API_KEY)}`;

    ws.current = new WebSocket(authenticatedUrl);

    ws.current.onopen = () => {
      setIsConnected(true);
      // Heartbeat: send ping every 15 seconds
      pingInterval.current = setInterval(() => {
        if (ws.current?.readyState === WebSocket.OPEN) {
          ws.current.send('ping');
        }
      }, 15000);
    };

    ws.current.onmessage = (event) => {
      if (event.data === 'ping' || event.data === 'pong') return;
      try {
        const data = JSON.parse(event.data);
        setLastMessage(data);
      } catch (err) {
        console.error('WebSocket message parsing failed:', err);
      }
    };

    ws.current.onclose = () => {
      setIsConnected(false);
      clearInterval(pingInterval.current);
      if (shouldReconnect.current) {
        reconnectTimeout.current = setTimeout(() => {
          connect();
        }, 3000);
      }
    };

    ws.current.onerror = (error) => {
      console.error('WebSocket Error:', error);
      ws.current.close();
    };
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      shouldReconnect.current = false;
      clearInterval(pingInterval.current);
      clearTimeout(reconnectTimeout.current);
      if (ws.current) {
        ws.current.close();
      }
    };
  }, [connect]);

  return { isConnected, lastMessage };
}
