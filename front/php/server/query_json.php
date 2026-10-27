<?php

// ---- IMPORTS ----
//------------------------------------------------------------------------------
// Check if authenticated
require_once $_SERVER['DOCUMENT_ROOT'] . '/php/templates/security.php';
// Get init.php
require dirname(__FILE__).'/../server/init.php';
// ---- IMPORTS ----

//------------------------------------------------------------------------------
// Handle incoming requests
if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $configRoot = getenv('NETALERTX_CONFIG') ?: '/data/config';
    $apiRoot = getenv('NETALERTX_API') ?: '/tmp/api';
    // Get query string parameter ?file=settings_table.json
    $file = isset($_GET['file']) ? $_GET['file'] : null;

    // Check if file parameter is provided
    if ($file) {
        // Define the folder where files are located
        if ($file == "workflows.json") 
        {
            $filePath = rtrim($configRoot, '/') . "/" . basename($file);
        } else
        {       
            $filePath = rtrim($apiRoot, '/') . "/" . basename($file);
        }

        // Check if the file exists
        if (file_exists($filePath)) {
            // Send the response back to the client
            header('Content-Type: application/json');
            echo file_get_contents($filePath);
        } else {
            // File not found response
            http_response_code(404);
            echo json_encode(["error" => "File not found"]);
        }
    } else {
        // Missing file parameter response
        http_response_code(400);
        echo json_encode(["error" => "Missing 'file' parameter"]);
    }
} else {
    header('Allow: GET');
    http_response_code(405);
    echo json_encode(["error" => "Method Not Allowed"]);
}
?>
