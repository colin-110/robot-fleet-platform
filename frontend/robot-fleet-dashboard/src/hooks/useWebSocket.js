import { useState, useEffect, useRef, useCallback } from 'react';

export function useWebSocket(url) {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState(null);
  const ws = useRef(null);
  const reconnectTimeout = useRef(null);
  const pingInterval = useRef(null);
  const shouldReconnect = useRef(true);

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return;

    ws.current = new WebSocket(url);

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
