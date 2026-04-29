# Copyright (C) 2026 Diego Rios Gomez
#
# This file is part of Non-intrusive monitoring for AlLoRa.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

# The class that offers the metrics object for the legacy HTML dashboard on the Gateway, and the corresponding lock for controlling its access.
# It also provides the analogous objects for the "node_status" (CONNECTED/DISCONNECTED/PENDING) which are also used in that previous dashboard.

from threading import Lock

metrics = {}
metrics_lock = Lock()

node_status = {}
node_status_lock = Lock()