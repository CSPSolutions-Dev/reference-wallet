# pyre-strict

# Copyright (c) The Libra Core Contributors
# SPDX-License-Identifier: Apache-2.0

import os

import libra_utils.libra
from pubsub import types


def test_bytes_parse() -> None:
    to_subaddr = os.urandom(8)

    # general metadata, version 0, to_subaddr present
    meta = libra_utils.libra.TransactionMetadata.from_bytes(
        b"\x01\x00" + b"\x01" + to_subaddr + b"\x00\x00"
    )
    assert meta.to_subaddress == to_subaddr

    # general metadata, version 0, reference not present
    from_subaddr = os.urandom(8)
    meta = libra_utils.libra.TransactionMetadata.from_bytes(
        b"\x01\x00" + b"\x01" + to_subaddr + b"\x01" + from_subaddr + b"\x00"
    )
    assert meta.to_subaddress == to_subaddr and meta.from_subaddress == from_subaddr

    # general metadata, version 0, everything present
    referenced_event = (123).to_bytes(8, byteorder="big", signed=False)
    meta = libra_utils.libra.TransactionMetadata.from_bytes(
        b"\x01\x00"
        + b"\x01"
        + to_subaddr
        + b"\x01"
        + from_subaddr
        + b"\x01"
        + referenced_event
    )
    assert (
        meta.to_subaddress == to_subaddr
        and meta.from_subaddress == from_subaddr
        and meta.referenced_event
        == int.from_bytes(referenced_event, byteorder="big", signed=False)
    )

    # travel rule metadata, version 0, off_chain_id present
    off_chain_reference_id = "aaaaaaaa".encode("utf-8")
    meta = libra_utils.libra.TransactionMetadata.from_bytes(
        b"\x02\x00" + b"\x01" + off_chain_reference_id
    )
    assert meta.off_chain_reference_id == off_chain_reference_id.decode("utf-8")

    # malformed, but shouldn't error out. expect empty TransactionMetadata object
    meta = libra_utils.libra.TransactionMetadata.from_bytes(
        b"\x01\x00" + b"\x01" + to_subaddr + b"\x01" + b"\x00" + b"\x01" + b"\x00"
    )
    assert meta and not (
        meta.to_subaddress or meta.from_subaddress or meta.referenced_event
    )
