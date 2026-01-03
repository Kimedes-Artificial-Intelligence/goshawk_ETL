#!/usr/bin/env python3
"""
Workflow state management for Sentinel data download workflow.
Handles saving and loading workflow configuration between different scripts.
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict


class WorkflowState:
    """Manages workflow state and configuration persistence"""

    def __init__(self, state_file: str = ".workflow_state.json"):
        """
        Initialize workflow state manager

        Args:
            state_file: Path to state file (default: .workflow_state.json)
        """
        self.state_file = Path(state_file)

    def save_workflow_state(self, config: Dict) -> bool:
        """
        Save workflow configuration to state file

        Args:
            config: Dictionary with workflow configuration
                   Expected keys: satellite, start_date, end_date, aoi_file, etc.

        Returns:
            bool: True if saved successfully
        """
        try:
            with open(self.state_file, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"⚠️  Error saving workflow state: {e}")
            return False

    def load_workflow_state(self) -> Optional[Dict]:
        """
        Load workflow configuration from state file

        Returns:
            Optional[Dict]: Configuration dictionary or None if not found
        """
        try:
            if not self.state_file.exists():
                return None

            with open(self.state_file, 'r') as f:
                config = json.load(f)

            return config
        except Exception as e:
            print(f"⚠️  Error loading workflow state: {e}")
            return None

    def clear_workflow_state(self) -> bool:
        """
        Clear workflow state file

        Returns:
            bool: True if cleared successfully
        """
        try:
            if self.state_file.exists():
                self.state_file.unlink()
            return True
        except Exception as e:
            print(f"⚠️  Error clearing workflow state: {e}")
            return False

    def update_workflow_state(self, updates: Dict) -> bool:
        """
        Update specific fields in workflow state

        Args:
            updates: Dictionary with fields to update

        Returns:
            bool: True if updated successfully
        """
        config = self.load_workflow_state()
        if config is None:
            config = {}

        config.update(updates)
        return self.save_workflow_state(config)
