"use client";

import { useEffect, useRef } from "react";

/** A arte da marca viva: linhas finas em "V" invertido subindo devagar até um ápice,
 *  surgindo embaixo e sumindo em cima (loop contínuo, sem emenda). É a mesma animação
 *  da tela de início do app desktop. Respeita "reduzir movimento" (fica parada). */
export default function RisingLines({ className = "", color = "201, 196, 255" }: { className?: string; color?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, raf = 0, inicio: number | null = null;
    const camadas = [
      { n: 34, speed: 0.012, spread: 1.25, alpha: 0.2, width: 1.0 },
      { n: 26, speed: 0.02, spread: 0.95, alpha: 0.58, width: 1.2 },
    ];
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = cv.getBoundingClientRect();
      W = r.width; H = r.height;
      cv.width = Math.max(1, W * dpr); cv.height = Math.max(1, H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const desenha = (t: number) => {
      ctx.clearRect(0, 0, W, H);
      const cx = W * 0.5, apex = H * 0.12, base = H * 1.08;
      for (const L of camadas) {
        for (let i = 0; i < L.n; i++) {
          const f = ((i / L.n) + t * L.speed) % 1;
          const y = base - (base - apex) * f;
          const half = (W * 0.62 * L.spread) * Math.pow(1 - f, 0.85) + 14;
          const drop = half * 0.78;
          const a = L.alpha * Math.sin(Math.PI * Math.min(1, f * 1.05)) * (0.55 + 0.45 * Math.sin(Math.PI * f));
          if (a <= 0.004) continue;
          ctx.strokeStyle = `rgba(${color}, ${a.toFixed(3)})`;
          ctx.lineWidth = L.width;
          ctx.beginPath();
          ctx.moveTo(cx - half, y + drop);
          ctx.lineTo(cx, y);
          ctx.lineTo(cx + half, y + drop);
          ctx.stroke();
        }
      }
      const g = ctx.createRadialGradient(cx, apex, 0, cx, apex, Math.max(W, H) * 0.32);
      g.addColorStop(0, `rgba(${color}, 0.10)`);
      g.addColorStop(1, `rgba(${color}, 0)`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    };
    const ro = new ResizeObserver(() => { resize(); if (reduce) desenha(0); });
    ro.observe(cv);
    resize();
    if (reduce) { desenha(0); return () => ro.disconnect(); }
    const frame = (ts: number) => {
      if (inicio === null) inicio = ts;
      desenha((ts - inicio) / 1000);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [color]);
  return <canvas ref={ref} aria-hidden className={`block h-full w-full ${className}`} />;
}
