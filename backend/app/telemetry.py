"""Azure Monitor (Application Insights) telemetry bootstrap.

Safe to call more than once per process; only the first call does work.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Guard flag so repeated calls (API lifespan + each worker process) only init once.
_initialized = False


def init_telemetry() -> None:
    global _initialized
    if _initialized:
        return

    # Telemetry is opt-in: without a connection string the app runs with it disabled.
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logger.warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set; Azure Monitor telemetry disabled"
        )
        return

    try:
        # Imports inside the try so a missing package disables telemetry instead of crashing.
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        # One call sets up OpenTelemetry traces/metrics/logs exporting to Azure Monitor.
        configure_azure_monitor(connection_string=connection_string)
        # Auto-traces every httpx request (e.g. the LLM REST calls) as a dependency span.
        HTTPXClientInstrumentor().instrument()

        try:
            from opentelemetry.instrumentation.openai import OpenAIInstrumentor

            # Optional: instruments the openai SDK, if the package is installed.
            OpenAIInstrumentor().instrument()
        except ImportError:
            logger.warning(
                "opentelemetry-instrumentation-openai not installed; "
                "skipping OpenAI instrumentation"
            )

        _initialized = True
        logger.info("Azure Monitor telemetry initialized")
    except Exception:
        logger.exception("Failed to initialize Azure Monitor telemetry")
