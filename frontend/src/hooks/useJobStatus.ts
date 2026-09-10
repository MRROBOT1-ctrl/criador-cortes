import { useEffect, useRef, useState } from "react";
import { getJobStatus, wsUrl } from "../api";
import type { JobOut } from "../types";

/**
 * Acompanha o status de um job via WebSocket nativo (ws/status/{job_id}).
 * Se o WS falhar por qualquer motivo (proxy bloqueando, etc.), cai para
 * polling em `GET /status/{job_id}` a cada 2s como fallback — garante que a
 * tela nunca fica travada mesmo se o WS não funcionar no ambiente do usuário.
 */
export function useJobStatus(jobId: string | null) {
  const [job, setJob] = useState<JobOut | null>(null);
  const [wsFailed, setWsFailed] = useState(false);

  // Ref com o último status conhecido — usado no onclose pra decidir se o
  // job já tinha terminado (fechar é normal) ou se caiu no meio do processo
  // (precisa acionar o polling de fallback). Vídeos longos (VOD de live,
  // horas de processamento) tornam essa queda no meio do caminho bem mais
  // provável do que num clipe curto — sem isso a tela ficava travada pra
  // sempre no último progresso recebido.
  const jobRef = useRef<JobOut | null>(null);
  jobRef.current = job;

  useEffect(() => {
    if (!jobId) return;
    setJob(null);
    setWsFailed(false);

    let cancelled = false;
    let socket: WebSocket | null = null;
    let gotAnyMessage = false;

    const isTerminal = () => {
      const status = jobRef.current?.status;
      return status === "completed" || status === "failed";
    };

    try {
      socket = new WebSocket(wsUrl(`/ws/status/${jobId}`));

      socket.onmessage = (event) => {
        if (cancelled) return;
        gotAnyMessage = true;
        try {
          const payload = JSON.parse(event.data) as JobOut & { error?: string };
          if (payload.error) {
            setWsFailed(true);
            return;
          }
          setJob(payload);
        } catch {
          // ignora mensagem malformada
        }
      };

      socket.onerror = () => {
        if (!cancelled && !gotAnyMessage) setWsFailed(true);
      };

      socket.onclose = () => {
        // Fecha sem nunca ter conectado direito, OU fecha no meio de um job
        // que ainda não terminou (queda de rede, restart do backend, proxy
        // derrubando conexão ociosa em jobs longos etc.) — nos dois casos,
        // cai pro polling em vez de travar a tela no último progresso visto.
        if (!cancelled && (!gotAnyMessage || !isTerminal())) setWsFailed(true);
      };
    } catch {
      setWsFailed(true);
    }

    return () => {
      cancelled = true;
      socket?.close();
    };
  }, [jobId]);

  // Fallback via polling quando o WS falhou (ou nunca conectou).
  const pollingRef = useRef<number | null>(null);
  useEffect(() => {
    if (!jobId || !wsFailed) return;

    const poll = async () => {
      try {
        const data = await getJobStatus(jobId);
        setJob(data);
        if (data.status === "completed" || data.status === "failed") {
          if (pollingRef.current) window.clearInterval(pollingRef.current);
        }
      } catch {
        // tenta de novo no próximo tick
      }
    };

    poll();
    pollingRef.current = window.setInterval(poll, 2000);
    return () => {
      if (pollingRef.current) window.clearInterval(pollingRef.current);
    };
  }, [jobId, wsFailed]);

  return job;
}
