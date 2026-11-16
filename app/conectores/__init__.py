"""Fuentes de datos automáticas: MADeCa lee periódicamente documentos
(SharePoint/OneDrive, Google Drive, Dropbox, SFTP, carpeta del servidor)
o expediciones de un ERP (Business Central, Odoo, SAP Business One,
Holded) y deja cada uno como borrador en la bandeja de revisión. Nada se
emite sin revisión humana (Art. 7 Orden FOM/2861/2012).

Las credenciales se guardan cifradas (Fernet) y nunca se devuelven al
navegador. Las URL que configura cada empresa no pueden apuntar a redes
privadas del servidor (protección frente a SSRF).
"""
