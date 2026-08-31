import type { Metadata } from "next";
import LegalPage from "../legal/LegalPage";

export const metadata: Metadata = {
  title: "Termos de Serviço — Singularity AI",
  description: "Regras de uso do Singularity AI.",
};

export default function Terms() {
  return <LegalPage file="terms" />;
}
