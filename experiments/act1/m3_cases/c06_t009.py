import asyncio


class SecurityAuditClient:
    def audit_sync(self, code: dict[str, str], context: str = "") -> dict:
        """Synchronous wrapper for use in LangGraph nodes."""
        try:
            return asyncio.run(self.audit(code, context))
        except Exception as e:
            print(f"  A2A security agent unavailable ({e}), using inline fallback")
            return self._inline_fallback(code)

    def _inline_fallback(self, code: dict[str, str]) -> dict:
        """Simple PII/secret check when A2A agent is unreachable."""
        for fn, content in code.items():
            _, detected = filter_pii(content)
            if detected:
                return {
                    "verdict": "needs_revision",
                    "findings": [{"severity": "high",
                                  "description": f"PII detected in {fn}: {detected}",
                                  "fix": "Remove PII from code"}],
                }
        return {"verdict": "approved", "findings": []}
