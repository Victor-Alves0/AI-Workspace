// Copiar para a área de transferência que FUNCIONA fora de HTTPS.
//
// `navigator.clipboard` só existe em contexto seguro (https/localhost) — no acesso
// pela LAN (http://192.168…, celular) ele é `undefined` e todo botão de copiar
// falhava em silêncio. O fallback usa um <textarea> temporário + execCommand.

export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // permissão negada/foco perdido: tenta o fallback do mesmo jeito
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
