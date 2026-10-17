// Mounts the parking countdown (parking.js) on the app page, as alertness.js
// mounts drowsy mode: a module of its own, so a test page can import
// parking.js without a card appearing on it.
import { mountParking } from "./parking.js";

mountParking();
