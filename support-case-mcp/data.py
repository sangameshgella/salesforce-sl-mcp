from typing import Dict, Any, List

# Mock Data extracted from "Silicon Labs - Use Case.docx"
CASES: Dict[str, Dict[str, Any]] = {
    "SL-COMP-00342817": {
        "id": "SL-COMP-00342817",
        "type": "Compliance / Certification Request",
        "component": "WFM200S022XNN3",
        "title": "EU-RED Compliance Verification",
        "description": "Customer needs confirmation that the module meets EU-RED cybersecurity requirements (EN 18032). Key capabilities: Secure boot, Firmware signature verification, Secure storage.",
        "status": "In Progress",
        "external_dependency": "TÜV SÜD",
        "resolution": "Requires both internal validation and external certification confirmation."
    },
    "SL-TECH-00335943": {
        "id": "SL-TECH-00335943",
        "type": "Technical Issue – Follow-Up",
        "protocol": "Firmware / SDK",
        "title": "Runtime behavior during application execution",
        "description": "Customer follow-up: 'We had reported this issue earlier. We understand that some changes were made by your team. Can you confirm what has been done so far...'",
        "status": "Monitoring",
        "fix_status": "Implemented",
        "validation_status": "Testing completed",
        "notes": "Case kept open for monitoring to ensure stability before closure."
    },
    "SL-CFG-00348210": {
        "id": "SL-CFG-00348210",
        "type": "Hardware / Firmware Configuration",
        "mcu": "EFR32MG24xx",
        "title": "Crystal Frequency Configuration for Matter Project",
        "description": "Customer using custom board with 38.4 MHz crystal, default config is different. Needs guidance on configuring crystal frequency and validating setup.",
        "status": "New",
        "resolution_steps": [
            "Confirm custom board uses 38.4 MHz external crystal",
            "Update SL_DEVICE_INIT_HFXO_FREQ = 38400000",
            "Update SL_DEVICE_INIT_DPLL_FREQ = 76800000",
            "Rebuild Matter application"
        ]
    }
}

MONITORING_DATA = {
    "SL-TECH-00335943": {
        "uptime": "99.99%",
        "errors_last_24h": 0,
        "latency_p95": "120ms",
        "status": "Stable"
    }
}

ENGINEERING_STATUS = {
    "SL-TECH-00335943": {
        "ticket_id": "ENG-1234",
        "status": "Done",
        "deployed_version": "v2.1.4",
        "qa_signoff": True
    }
}
