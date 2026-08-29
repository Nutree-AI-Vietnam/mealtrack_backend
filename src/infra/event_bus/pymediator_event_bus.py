"""
PyMediator-based event bus implementation.
"""

import asyncio
import copy
import inspect
import logging
from typing import Any, TypeVar

from pymediator import Mediator as PyMediator
from pymediator import SingletonRegistry

from src.api.exceptions import MealTrackException
from src.domain.events.base import DomainEvent, Event, EventHandler
from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationCreationError,
)

from .event_bus import EventBus

logger = logging.getLogger(__name__)

T = TypeVar("T")


class EventRequest:
    """Wrapper to make our Events compatible with pymediator Request protocol."""

    def __init__(self, event: Event):
        self._event = event

    @property
    def event(self) -> Event:
        return self._event


class AsyncPyMediatorHandlerAdapter:
    """Async-aware adapter for our EventHandlers."""

    def __init__(self, event_handler: EventHandler):
        self._event_handler = event_handler

    async def handle(self, request: Any) -> Any:
        """Handle the request asynchronously."""
        # Extract the actual event from the wrapper
        if hasattr(request, "event"):
            actual_event = request.event
        else:
            actual_event = request

        return await self._event_handler.handle(actual_event)


class PyMediatorEventBus(EventBus):
    """
    Event bus implementation using pymediator library.

    This implementation wraps pymediator to provide compatibility with our
    event-driven architecture while leveraging pymediator's features.
    Uses async-native execution without thread pools for proper event loop handling.
    """

    def __init__(self):
        # Use SingletonRegistry to ensure handlers are reused
        registry = SingletonRegistry()
        self._mediator = PyMediator(registry=registry)
        self._event_type_mapping: dict[type[Event], type[EventRequest]] = {}
        self._domain_event_subscribers: dict[type[DomainEvent], list[Any]] = {}
        # Store direct handler references for async execution
        self._async_handlers: dict[type[Event], EventHandler] = {}

    def register_handler(self, event_type: type[Event], handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        # Store the handler directly for async execution
        self._async_handlers[event_type] = handler

        # Create a unique wrapper class for this event type
        wrapper_class = type(
            f"{event_type.__name__}Request",
            (EventRequest,),
            {"__init__": lambda self, event: EventRequest.__init__(self, event)},
        )

        # Store the mapping
        self._event_type_mapping[event_type] = wrapper_class

        # Create async handler adapter
        adapter_class = type(
            f"{handler.__class__.__name__}AsyncAdapter",
            (AsyncPyMediatorHandlerAdapter,),
            {
                "__init__": lambda self: AsyncPyMediatorHandlerAdapter.__init__(
                    self, handler
                )
            },
        )

        # Register with pymediator
        self._mediator.registry.register(wrapper_class, adapter_class)

    @staticmethod
    def _fresh_uow_copy(handler: EventHandler) -> EventHandler:
        """Return a shallow handler copy with a fresh no-arg UnitOfWork when possible."""
        uow = getattr(handler, "uow", None)
        if uow is None:
            return handler

        uow_class = uow.__class__
        try:
            signature = inspect.signature(uow_class)
        except (TypeError, ValueError):
            return handler

        required_params = [
            param
            for param in signature.parameters.values()
            if param.default is inspect.Parameter.empty
            and param.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if required_params:
            return handler

        handler_copy = copy.copy(handler)
        handler_copy.uow = uow_class()
        return handler_copy

    def subscribe(self, event_type: type[DomainEvent], handler) -> None:
        """Subscribe to domain events."""
        if event_type not in self._domain_event_subscribers:
            self._domain_event_subscribers[event_type] = []

        self._domain_event_subscribers[event_type].append(handler)
        logger.debug(f"Subscribed to {event_type.__name__}")

    async def send(self, event: Event) -> Any:
        """Send a command/query and get the result."""
        event_type = type(event)

        if event_type not in self._async_handlers:
            raise ValueError(f"No handler registered for {event_type.__name__}")

        try:
            # Get the handler directly and execute it asynchronously
            handler = self._async_handlers[event_type]

            # Ensure stateful handlers don't share a UnitOfWork/session across requests.
            handler = self._fresh_uow_copy(handler)

            # Check if handler is async
            if inspect.iscoroutinefunction(handler.handle):
                result = await handler.handle(event)
            else:
                # For sync handlers, execute directly (no thread pool needed)
                result = handler.handle(event)

            # Handle domain events if returned
            if isinstance(result, list) and all(
                isinstance(e, DomainEvent) for e in result
            ):
                logger.debug(
                    f"Publishing {len(result)} domain events from command result"
                )
                for domain_event in result:
                    await self.publish(domain_event)
            elif isinstance(result, dict) and "events" in result:
                events = result.get("events", [])
                logger.debug(
                    f"Publishing {len(events)} domain events from command result"
                )
                for domain_event in events:
                    if isinstance(domain_event, DomainEvent):
                        await self.publish(domain_event)
            return result

        except (
            MealTrackException,
            AIUnavailableError,
            AIOutputValidationError,
            MealRecommendationCreationError,
        ) as e:
            # Controlled application exceptions and degraded AI-provider failures
            # are converted to proper HTTP responses by the API layer. Keep this
            # at debug so routine control flow does not produce duplicate ERRORs.
            logger.debug(f"Application exception handling {event_type.__name__}: {str(e)}")
            raise
        except Exception as e:
            logger.error(
                f"Error handling {event_type.__name__}: {str(e)}", exc_info=True
            )
            raise

    async def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to all subscribers."""
        event_type = type(event)

        if event_type not in self._domain_event_subscribers:
            logger.debug(f"No subscribers for {event_type.__name__}")
            return

        subscribers = self._domain_event_subscribers[event_type]
        logger.debug(
            f"Publishing {event_type.__name__} to {len(subscribers)} subscribers"
        )

        # Execute subscribers concurrently
        tasks = []
        for subscriber in subscribers:
            if asyncio.iscoroutinefunction(subscriber):
                tasks.append(subscriber(event))
            else:
                # Wrap sync handlers in async
                async def async_wrapper(handler, evt):
                    return handler(evt)

                tasks.append(async_wrapper(subscriber, event))

        if tasks:
            logger.debug(f"Starting execution for {event_type.__name__}")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Subscriber {i} for {event_type.__name__} failed: {result}",
                        exc_info=result,
                    )
            logger.debug(
                f"Processing completed for {event_type.__name__}"
            )

    def close(self):
        """Close event bus resources."""
        logger.info("Event bus closed")
