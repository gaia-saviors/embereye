/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm Basalt palette
        basalt: {
          DEFAULT: "#0C0B0E",
          100: "#141318",
          200: "#1C1A22",
          300: "#25232C",
          400: "#383444",
        },
        // Brand & semantic accents
        terra: {
          DEFAULT: "#E04F33",
          glow: "rgba(224,79,51,0.18)",
        },
        // Sensor accents
        amber: {
          DEFAULT: "#E5A93C",
        },
        jade: {
          DEFAULT: "#10B981",
        },
        // Alert
        coral: {
          DEFAULT: "#F43F5E",
        },
        teal: {
          DEFAULT: "#2DD4BF",
        },
        // Text hierarchy
        alabaster: "#F4F3F6",
        pewter: "#A09CA8",
        muted: "#686472",
        // Legacy aliases kept for backward compat (some components use ink/ember/viirs/mist)
        ink: {
          950: "#0C0B0E",
          900: "#141318",
          850: "#1C1A22",
          800: "#25232C",
          700: "#383444",
          600: "#4A4557",
          500: "#686472",
        },
        ember: {
          300: "#F0A87A",
          400: "#E87058",
          500: "#E04F33",
          600: "#C94228",
        },
        viirs: "#10B981",
        modis: "#E5A93C",
        alert: "#F43F5E",
        mist: "#A09CA8",
        paper: "#F4F3F6",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ["Space Grotesk", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
