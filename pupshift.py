#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа pupshift: python3 pupshift.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pupshift.app import main

if __name__ == "__main__":
    main()
