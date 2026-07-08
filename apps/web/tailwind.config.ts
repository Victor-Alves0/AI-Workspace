import type { Config } from "tailwindcss";
import defaultTheme from "tailwindcss/defaultTheme";

// Design tokens: os valores reais vivem em CSS variables (globals.css) como
// triplas RGB, permitindo modificadores de opacidade (ex.: bg-accent/15) e
// futura troca de tema sem tocar nos componentes.
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // fundo do chat (centro) e da barra lateral / controles
        bg: token("c-bg"),
        sidebar: token("c-sidebar"),
        // hierarquia de superfícies (mais claro = mais elevado)
        surface: token("c-surface"),
        surface2: token("c-surface2"),
        hover: token("c-hover"),
        border: token("c-border"),
        // cor de destaque — usar com moderação (CTAs, estados ativos, marca)
        accent: token("c-accent"),
        "accent-hover": token("c-accent-hover"),
        muted: token("c-muted"),
        ink: token("c-ink"), // texto principal
        "ink-soft": token("c-ink-soft"), // texto secundário
      },
      fontFamily: {
        sans: ["var(--font-sans)", ...defaultTheme.fontFamily.sans],
        mono: ["var(--font-mono)", ...defaultTheme.fontFamily.mono],
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
      boxShadow: {
        // elevação: só onde faz sentido (menus, modais, promptbox)
        menu: "0 4px 24px rgb(0 0 0 / 0.45), 0 1px 3px rgb(0 0 0 / 0.3)",
        modal: "0 24px 64px rgb(0 0 0 / 0.55), 0 4px 16px rgb(0 0 0 / 0.35)",
        prompt: "0 8px 30px rgb(0 0 0 / 0.35)",
      },
      transitionDuration: {
        DEFAULT: "180ms",
      },
    },
  },
  plugins: [],
};

export default config;
