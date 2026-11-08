import { useCallback, useState, useEffect, useRef } from 'react';
import { getTicket } from '../utils/ticket';

export function useWebSocket(url, onMessage, onOpen) {
  const [isConnected, setIsConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const socketRef = useRef(null);

  // Keep the latest callback without re-opening the socket on every render.
  useEffect(() => {
    onMessageRef.current = onMessage;
    onOpenRef.current = onOpen;
  }, [onMessage, onOpen]);

  useEffect(() => {
    let ws = null;
    let reconnectTimeout = null;
    let pingInterval = null;
    let shouldReconnect = true;

    const scheduleReconnect = () => {
      if (shouldReconnect) {
        reconnectTimeout = setTimeout(connect, 3000);
      }
    };

    const connect = async () => {
      if (ws?.readyState === WebSocket.OPEN) return;

      // A handshake cannot carry a custom header, so the credential has to ride
      // in the query string. That is why it is a short-lived scoped ticket and
      // not the master API key - see src/utils/ticket.js.
      let ticket;
      try {
        ticket = await getTicket();
      } catch (err) {
        console.error('Could not obtain a console ticket:', err);
        scheduleReconnect();
        return;
      }

      // The component can unmount while that request is in flight; opening a
      // socket now would leak one no cleanup has a reference to.
      if (!shouldReconnect) return;

      const separator = url.includes('?') ? '&' : '?';
      ws = new WebSocket(`${url}${separator}ticket=${encodeURIComponent(ticket)}`);
      socketRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        // A new connection knows nothing of this client: resend its state
        // (subscriptions, watched robots) through this socket.
        onOpenRef.current?.((data) => ws.send(JSON.stringify(data)));
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
        scheduleReconnect();
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

  // Send a control message; dropped while disconnected (onOpen resends state).
  const send = useCallback((data) => {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data));
  }, []);

  return { isConnected, send };
}
