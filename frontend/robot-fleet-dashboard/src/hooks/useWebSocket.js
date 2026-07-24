import { useState, useEffect, useRef } from 'react';

const WS_API_KEY = import.meta.env.VITE_WS_API_KEY || 'fleet-secret-key-2026';

export function useWebSocket(url, onMessage) {
  const [isConnected, setIsConnected] = useState(false);
  const onMessageRef = useRef(onMessage);

  // Keep the latest callback without re-opening the socket on every render.
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    let ws = null;
    let reconnectTimeout = null;
    let pingInterval = null;
    let shouldReconnect = true;

    const connect = () => {
      if (ws?.readyState === WebSocket.OPEN) return;

      // Append the API key as a query parameter for WebSocket auth.
      const separator = url.includes('?') ? '&' : '?';
      const authenticatedUrl = `${url}${separator}api_key=${encodeURIComponent(WS_API_KEY)}`;
      ws = new WebSocket(authenticatedUrl);

      ws.onopen = () => {
        setIsConnected(true);
        // Heartbeat: keep the connection warm.
        pingInterval = setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) ws.send('ping');
        }, 15000);
      };

      ws.onmessage = (event) => {
        if (event.data === 'ping' || event.data === 'pong') return;
        try {
          const data = JSON.parse(event.data);
          onMessageRef.current?.(data);
        } catch (err) {
          console.error('WebSocket message parsing failed:', err);
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        clearInterval(pingInterval);
        if (shouldReconnect) {
          reconnectTimeout = setTimeout(connect, 3000);
        }
      };

      ws.onerror = (error) => {
        console.error('WebSocket Error:', error);
        ws?.close();
      };
    };

    connect();

    return () => {
      shouldReconnect = false;
      clearInterval(pingInterval);
      clearTimeout(reconnectTimeout);
      ws?.close();
    };
  }, [url]);

  return { isConnected };
}
