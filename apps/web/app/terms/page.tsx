import type { Metadata } from "next";
import LegalPage from "../legal/LegalPage";

export const metadata: Metadata = {
  title: "Termos de Serviço — AI Workspace",
  description: "Regras de uso do AI Workspace.",
};

export default function Terms() {
  return <LegalPage file="terms" />;
}
