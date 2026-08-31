import type { Metadata } from "next";
import LegalPage from "../legal/LegalPage";

export const metadata: Metadata = {
  title: "Política de Privacidade — Singularity AI",
  description: "Como o Singularity AI trata seus dados.",
};

export default function Privacy() {
  return <LegalPage file="privacy" />;
}
