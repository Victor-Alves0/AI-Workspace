// Lê um arquivo de imagem, redimensiona (mantendo proporção, lado maior = `max`)
// e devolve um data URL JPEG. Mantém o avatar pequeno o bastante p/ caber no
// perfil (JSONB) sem estourar o limite do backend.
export async function fileToAvatarDataUrl(file: File, max = 256, quality = 0.85): Promise<string> {
  if (!file.type.startsWith("image/")) {
    throw new Error("Selecione um arquivo de imagem");
  }
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error("Falha ao ler o arquivo"));
    r.readAsDataURL(file);
  });

  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const i = new Image();
    i.onload = () => resolve(i);
    i.onerror = () => reject(new Error("Imagem inválida"));
    i.src = dataUrl;
  });

  const scale = Math.min(1, max / Math.max(img.width, img.height));
  const w = Math.round(img.width * scale);
  const h = Math.round(img.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas indisponível");
  ctx.drawImage(img, 0, 0, w, h);
  return canvas.toDataURL("image/jpeg", quality);
}

// Lê uma imagem para envio ao modelo (visão): redimensiona p/ um lado máx. maior
// (1024 por padrão) e devolve data URL JPEG — cabe no anexo sem estourar limites.
export function fileToImageDataUrl(file: File, max = 1024, quality = 0.85): Promise<string> {
  return fileToAvatarDataUrl(file, max, quality);
}

// Lê um arquivo de texto (limite de caracteres) para anexar como contexto textual.
export async function fileToText(file: File, maxChars = 100_000): Promise<string> {
  const text = await file.text();
  return text.length > maxChars ? text.slice(0, maxChars) + "\n…(truncado)" : text;
}

// Lê um arquivo binário (PDF/DOCX/XLSX…) como base64 puro (sem prefixo data:)
// para o servidor extrair o texto. Processa em blocos p/ não estourar a pilha.
export async function fileToBase64(file: File): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < buf.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, Array.from(buf.subarray(i, i + CHUNK)));
  }
  return btoa(bin);
}
