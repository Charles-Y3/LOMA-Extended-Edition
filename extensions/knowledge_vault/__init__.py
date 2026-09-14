# -*- coding: utf-8 -*-
# Install the Document Intelligence → Knowledge Vault pickle-compat shim BEFORE anything
# else imports/loads vault data, so existing users' document_intelligence-tagged pickles
# still unpickle after the rename (see _compat_document_intelligence).
from extensions.knowledge_vault import _compat_document_intelligence as _compat

_compat.install()

from extensions.knowledge_vault.extension import KnowledgeVaultExtension  # noqa: E402

__all__ = ["KnowledgeVaultExtension"]
