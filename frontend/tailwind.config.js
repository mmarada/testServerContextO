/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ctx: {
          bg: "#0d0d0d",
          card: "#141414",
          border: "#27272a",
          accent: "#22d3ee",
          err: "#f87171",
          ok: "#4ade80",
          warn: "#fbbf24",
          muted: "#71717a",
          text: "#d4d4d8",
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        spin: {
          to: { transform: "rotate(360deg)" },
        },
      },
      animation: {
        "pulse-dot": "pulseDot 1.2s ease-in-out infinite",
        spin: "spin 0.8s linear infinite",
      },
    },
  },
  plugins: [],
};
