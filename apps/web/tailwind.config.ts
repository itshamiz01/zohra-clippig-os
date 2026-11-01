import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#171713",
        paper: "#f4f1e9",
        lime: "#d8ff5f",
        coral: "#ff6b52",
        mist: "#ddded7"
      },
      boxShadow: {
        panel: "0 18px 60px rgba(23,23,19,.08)"
      }
    }
  },
  plugins: []
};

export default config;
