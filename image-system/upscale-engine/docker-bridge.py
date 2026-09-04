#!/usr/bin/env python3

import argparse
import asyncio
import signal


async def relay(reader, writer):
    try:
        while True:
            data = await reader.read(
                1024 * 1024
            )

            if not data:
                return

            writer.write(data)
            await writer.drain()

    except asyncio.CancelledError:
        raise

    except (
        ConnectionError,
        BrokenPipeError,
        OSError,
    ):
        return


async def close_writer(writer):
    if writer is None:
        return

    try:
        if not writer.is_closing():
            writer.close()
    except Exception:
        pass

    try:
        await asyncio.wait_for(
            writer.wait_closed(),
            timeout=1.0,
        )
        return

    except (
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
    ):
        pass

    # A TCP socket that refuses graceful close must not
    # keep the complete bridge process alive indefinitely.
    try:
        transport = writer.transport

        if transport is not None:
            transport.abort()

    except Exception:
        pass


async def handle(
    client_reader,
    client_writer,
    target_host,
    target_port,
):
    target_writer = None
    tasks = []

    try:
        try:
            target_reader, target_writer = (
                await asyncio.open_connection(
                    target_host,
                    target_port,
                )
            )

        except (
            ConnectionError,
            OSError,
        ):
            return

        tasks = [
            asyncio.create_task(
                relay(
                    client_reader,
                    target_writer,
                )
            ),
            asyncio.create_task(
                relay(
                    target_reader,
                    client_writer,
                )
            ),
        ]

        # A TCP relay session is finished as soon as
        # either direction reaches EOF/fails.
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    except asyncio.CancelledError:
        # main() deliberately cancels connection handlers
        # during controlled shutdown.
        raise

    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        # Both socket directions have a hard bounded
        # shutdown time.
        await asyncio.gather(
            close_writer(target_writer),
            close_writer(client_writer),
            return_exceptions=True,
        )


async def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--listen-host",
        required=True,
    )
    parser.add_argument(
        "--listen-port",
        required=True,
        type=int,
    )
    parser.add_argument(
        "--target-host",
        required=True,
    )
    parser.add_argument(
        "--target-port",
        required=True,
        type=int,
    )

    args = parser.parse_args()

    stop = asyncio.Event()
    connections = set()

    def client_connected(
        reader,
        writer,
    ):
        task = asyncio.create_task(
            handle(
                reader,
                writer,
                args.target_host,
                args.target_port,
            )
        )

        connections.add(task)

        task.add_done_callback(
            connections.discard
        )

    server = await asyncio.start_server(
        client_connected,
        args.listen_host,
        args.listen_port,
        reuse_address=True,
    )

    sockets = ", ".join(
        str(sock.getsockname())
        for sock in server.sockets or []
    )

    print(
        "[AAG-UPSCALE-BRIDGE] "
        f"listening on {sockets}",
        flush=True,
    )

    loop = asyncio.get_running_loop()

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        try:
            loop.add_signal_handler(
                sig,
                stop.set,
            )
        except NotImplementedError:
            pass

    await stop.wait()

    print(
        "[AAG-UPSCALE-BRIDGE] "
        "shutdown requested",
        flush=True,
    )

    # Phase 1:
    # Reject all new connections.
    server.close()

    await server.wait_closed()

    # Phase 2:
    # Cancel every active relay session.
    current = list(connections)

    for task in current:
        if not task.done():
            task.cancel()

    # Every connection has bounded socket cleanup,
    # so this should normally finish almost immediately.
    if current:
        done, pending = await asyncio.wait(
            current,
            timeout=5.0,
        )

        if pending:
            print(
                "[AAG-UPSCALE-BRIDGE] "
                f"forcing socket cleanup for "
                f"{len(pending)} connection task(s)",
                flush=True,
            )

            for task in pending:
                task.cancel()

            await asyncio.wait(
                pending,
                timeout=2.0,
            )

    print(
        "[AAG-UPSCALE-BRIDGE] "
        "shutdown complete",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
